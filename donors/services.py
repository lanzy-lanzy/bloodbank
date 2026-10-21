"""DonorEligibilityService — configurable eligibility engine.

SAFETY: The *mechanism* lives here; the *values* (intervals, age limits,
weight minimums, deferral periods) are institutional configuration stored in
SystemSetting (category "eligibility"). Nothing clinical is hard-coded. When a
rule value is not configured, the engine returns REQUIRES_STAFF_REVIEW rather
than inventing a threshold. Final eligibility is always a staff decision —
the engine only summarises configured rules.
"""
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from core.text import uppercase, uppercase_fields
from donors.interview import (ALL_ITEMS, FEMALE_SECTION, SECTION_BY_KEY, SHEET_DATE_FIELDS,
                              SHEET_TEXT_FIELDS)

# Donor fields the interview sheet prints: stored in the same upper case the
# document shows, so the record and the paper can never disagree.
DONOR_TEXT_FIELDS = ("first_name", "middle_name", "last_name", "email",
                     "contact_number", "address", "municipality", "province")

# SystemSetting keys used by the engine (values set by the institution):
ELIGIBILITY_KEYS = {
    "min_donation_interval_days": ("int", "Minimum days between whole-blood donations"),
    "min_age_years": ("int", "Minimum donor age"),
    "max_age_years": ("int", "Maximum donor age"),
    "min_weight_kg": ("int", "Minimum donor weight (kg), checked at screening"),
    "max_donations_per_year": ("int", "Maximum donations per rolling 12 months"),
}


@dataclass
class EligibilityResult:
    status: str  # ELIGIBLE | NOT_ELIGIBLE | DEFERRED | REQUIRES_STAFF_REVIEW
    reasons: list = field(default_factory=list)
    next_eligible_date: object = None

    @property
    def is_eligible(self):
        return self.status == "ELIGIBLE"


class DonorEligibilityService:
    @staticmethod
    def _rule(key):
        from settings_app.services import get_setting
        return get_setting(key, None)

    @staticmethod
    def evaluate(donor, when=None) -> EligibilityResult:
        when = when or timezone.now()
        today = when.date() if hasattr(when, "date") else when
        reasons = []
        review_reasons = []

        # 1. Donor status (institutional decision recorded on the donor)
        if donor.status == Donor_Status.PERM_DEFERRED:
            return EligibilityResult("NOT_ELIGIBLE",
                                     [f"Permanently deferred. {donor.deferral_reason}".strip()])
        if donor.status == Donor_Status.BLACKLISTED:
            return EligibilityResult("NOT_ELIGIBLE",
                                     [f"Restricted/blacklisted. {donor.deferral_reason}".strip()])
        if donor.status == Donor_Status.INACTIVE:
            return EligibilityResult("NOT_ELIGIBLE", ["Donor record is inactive."])
        if donor.status == Donor_Status.TEMP_DEFERRED:
            until = donor.deferred_until
            if until and until > today:
                return EligibilityResult("DEFERRED",
                                         [f"Temporarily deferred until {until:%b %d, Y}. {donor.deferral_reason}".strip()],
                                         next_eligible_date=until)
            reasons.append("Previous temporary deferral period has passed — staff review advised.")

        # 2. Latest screening outcome
        latest_screening = donor.screenings.order_by("-performed_at").first()
        if latest_screening:
            if latest_screening.result == "REJECTED":
                return EligibilityResult("NOT_ELIGIBLE",
                                         [f"Rejected at screening on {latest_screening.performed_at:%b %d, Y}."])
            if latest_screening.result == "REQUIRES_REVIEW":
                review_reasons.append("Latest screening outcome is 'Requires review'.")
            if latest_screening.result == "DEFERRED" and latest_screening.deferral_days:
                until = (latest_screening.performed_at + timedelta(days=latest_screening.deferral_days)).date()
                if until > today:
                    return EligibilityResult("DEFERRED",
                                             [f"Deferred by screening until {until:%b %d, Y} ({latest_screening.deferral_days} days)."],
                                             next_eligible_date=until)

        # 3. Age window (configured)
        min_age = DonorEligibilityService._rule("min_age_years")
        max_age = DonorEligibilityService._rule("max_age_years")
        if min_age is not None and donor.age < int(min_age):
            return EligibilityResult("NOT_ELIGIBLE",
                                     [f"Donor is {donor.age}; configured minimum age is {min_age}."])
        if max_age is not None and donor.age > int(max_age):
            return EligibilityResult("NOT_ELIGIBLE",
                                     [f"Donor is {donor.age}; configured maximum age is {max_age}."])

        # 4. Donation interval (configured)
        interval = DonorEligibilityService._rule("min_donation_interval_days")
        last = donor.last_donation
        next_eligible = None
        if last:
            if interval is None:
                review_reasons.append(
                    "Minimum donation interval is not configured; cannot verify interval since last donation "
                    f"({last.donation_date:%b %d, Y})."
                )
            else:
                next_eligible = last.donation_date + timedelta(days=int(interval))
                if next_eligible > today:
                    return EligibilityResult(
                        "DEFERRED",
                        [f"Last donation {last.donation_date:%b %d, Y}; configured interval is {interval} days."],
                        next_eligible_date=next_eligible,
                    )

        # 5. Rolling-year donation cap (configured, optional)
        cap = DonorEligibilityService._rule("max_donations_per_year")
        if cap is not None:
            since = today - timedelta(days=365)
            count = donor.donations.filter(
                status__in=["COLLECTED", "RELEASED"], donation_date__gte=since
            ).count()
            if count >= int(cap):
                return EligibilityResult(
                    "NOT_ELIGIBLE",
                    [f"Donor has {count} donation(s) in the past 12 months; configured maximum is {cap}."],
                )

        if review_reasons:
            return EligibilityResult("REQUIRES_STAFF_REVIEW", review_reasons + reasons, next_eligible_date=next_eligible)
        return EligibilityResult("ELIGIBLE", reasons or ["All configured eligibility rules satisfied."],
                                 next_eligible_date=next_eligible)


# Local alias to avoid importing Donor at module import time (app-loading order).
class Donor_Status:
    ACTIVE = "ACTIVE"
    TEMP_DEFERRED = "TEMP_DEFERRED"
    PERM_DEFERRED = "PERM_DEFERRED"
    INACTIVE = "INACTIVE"
    BLACKLISTED = "BLACKLISTED"


class InterviewSheetService:
    """The printable donor interview sheet: generation, pre-fill and saving.

    The sheet is generated automatically as soon as a donor record exists, so
    the clinic never starts from a blank page: identity, address and contact
    details come from the donor record, and the questionnaire the applicant
    answered at registration pre-answers the same items here.

    A blank answer stays blank: the system does not infer "No" from silence,
    because a printed form must never claim an answer the donor did not give.
    Saving never changes donor status or eligibility - this records the
    interview, it does not decide anything (agents.md rule 1).
    """

    @staticmethod
    def ensure_for_donor(donor, *, registration=None, actor=None, request=None):
        """Return the donor's sheet, generating and pre-filling it once."""
        from audit import services as audit
        from donors.models import DonorInterviewSheet, InterviewAnswer

        sheet, created = DonorInterviewSheet.objects.get_or_create(
            donor=donor,
            defaults={"registration": registration,
                      "address_street": uppercase(donor.registered_address)},
        )
        if not created:
            return sheet

        InterviewAnswer.objects.bulk_create(
            [InterviewAnswer(sheet=sheet, item_key=item.key) for item in ALL_ITEMS])
        interview = getattr(registration, "interview", None) if registration else None
        prefill_note = ""
        if interview is not None:
            copied = InterviewSheetService._apply_signup_declaration(sheet, interview)
            if copied:
                prefill_note = (f" {copied} answer(s) pre-filled from the registration "
                                "self-declaration.")
        audit.log(request, user=actor, action="INTERVIEW_SHEET_CREATED", module="donors",
                  obj=sheet,
                  description=(f"Interview sheet generated for donor {donor.donor_code} "
                               f"({donor.full_name})." + prefill_note))
        return sheet

    @staticmethod
    def _declaration_answers(interview, *, female):
        """{item key: "YES"|"NO"} the sign-up questionnaire answers, in memory.

        The sign-up form asks the printed sheet's own YES/NO items, one field
        per item (``Q07`` -> ``q07``), so an answer is copied to the item it
        answers and a blank stays blank. The female-donor block is skipped
        unless the donor is female - copying a "No" there would record an
        answer nobody was asked for. Shared by the generation path and the
        pending-registration preview so both show the same pre-fill.
        """
        answers = {}
        for item in ALL_ITEMS:
            if item.kind != "YESNO":
                continue
            if not female and SECTION_BY_KEY.get(item.key) == FEMALE_SECTION:
                continue
            value = getattr(interview, item.key.lower(), None)
            if value is not None:
                answers[item.key] = "YES" if value else "NO"
        return answers

    @staticmethod
    def _apply_signup_declaration(sheet, interview):
        """Copy the sign-up declaration onto the official items it answers."""
        from donors.models import InterviewAnswer

        rows = sheet.answer_map()
        # The female-donor block is not asked of anybody else: copying a
        # "No" there would record an answer the donor was never asked for.
        female = sheet.donor.sex == "FEMALE"
        values = InterviewSheetService._declaration_answers(interview, female=female)
        changed = []
        for key, answer in values.items():
            row = rows.get(key) or InterviewAnswer(sheet=sheet, item_key=key)
            row.answer = answer
            changed.append(row)
        if changed:
            InterviewAnswer.objects.bulk_update(changed, ["answer"])
        return len(changed)

    @staticmethod
    def preview_for_registration(registration):
        """The sheet approval *will* create, built in memory for staff review.

        Nothing is written and nothing is audited: this is the same pre-fill
        :meth:`ensure_for_donor` performs at approval, so staff can verify the
        data while the registration is still PENDING. The transient donor
        carries a PREVIEW- reference (no donor code exists yet) and the
        applicant's declared sex, so the female-donor block is shown only when
        approval will show it.

        Returns ``(sheet, answers)`` where ``answers`` is a plain
        ``{item key: InterviewAnswer}`` map: ``sheet.answers`` must not be
        queried because the transient sheet has no primary key.
        """
        return InterviewSheetService._preview_for(
            registration,
            interview=getattr(registration, "interview", None),
            reference=f"PREVIEW-{registration.pk:05d}",
        )

    @staticmethod
    def preview_for_application(data, answers):
        """The sheet a validated application will create, before it is submitted.

        ``data`` is the registration form's ``cleaned_data`` and ``answers`` the
        dict its ``interview_answers()`` produces, so the service stays unaware
        of the form. Nothing is written: the applicant reviews exactly what the
        clinic will receive without creating an account, a registration, a donor
        record or an interview sheet.
        """
        from accounts.models import RegistrationInterview, RegistrationRequest

        registration = RegistrationRequest(
            role=data["role"],
            first_name=data["first_name"],
            middle_name=data.get("middle_name") or "",
            last_name=data["last_name"],
            email=data["email"],
            phone=data["phone"],
            sex=data.get("sex") or RegistrationRequest.Sex.UNDISCLOSED,
            date_of_birth=data.get("date_of_birth"),
            address=data.get("address") or "",
            municipality=data.get("municipality") or "",
            province=data.get("province") or "",
            blood_type=data.get("blood_type"),
        )
        interview = RegistrationInterview(**(answers or {}))
        return InterviewSheetService._preview_for(
            registration, interview=interview, reference="PREVIEW")

    @staticmethod
    def _preview_for(registration, *, interview, reference):
        """Shared in-memory donor + sheet + pre-filled answers for a preview."""
        from donors.models import Donor, DonorInterviewSheet, InterviewAnswer

        donor = Donor(
            donor_code=reference,
            first_name=registration.first_name,
            middle_name=registration.middle_name,
            last_name=registration.last_name,
            date_of_birth=registration.date_of_birth,
            sex=registration.sex or Donor.Sex.UNDISCLOSED,
            contact_number=registration.phone,
            email=registration.email,
            address=registration.address,
            municipality=registration.municipality,
            province=registration.province,
            blood_type=registration.blood_type,
        )
        # Registrations are normalised when submitted; a row that predates that
        # rule (or form data) is normalised here so the preview always shows
        # what will print.
        uppercase_fields(donor, DONOR_TEXT_FIELDS)
        sheet = DonorInterviewSheet(
            donor=donor,
            registration=registration,
            address_street=uppercase(donor.registered_address),
        )
        # The female-donor block follows the donor's sex, exactly as it will
        # when approval generates the real sheet.
        female = donor.sex == Donor.Sex.FEMALE
        values = (InterviewSheetService._declaration_answers(interview, female=female)
                  if interview is not None else {})
        answers = {key: InterviewAnswer(item_key=key, answer=value)
                   for key, value in values.items()}
        return sheet, answers

    @staticmethod
    def initial_data(sheet):
        """Initial values for the editable form, straight from the record."""
        data = {name: getattr(sheet, name) for name in SHEET_TEXT_FIELDS}
        data.update({name: getattr(sheet, name) for name in SHEET_DATE_FIELDS})
        data["cue_choice"] = sheet.cue_choice
        data["mark_complete"] = sheet.is_completed
        for key, answer in sheet.answer_map().items():
            data[f"answer_{key}"] = answer.answer
            data[f"remarks_{key}"] = answer.remarks
        return data

    @staticmethod
    def progress(sheet):
        """(answered YES/NO items, YES/NO items in total, remarks written)."""
        yesno = {item.key for item in ALL_ITEMS if item.kind == "YESNO"}
        rows = list(sheet.answers.all())
        answered = sum(1 for row in rows if row.item_key in yesno and row.answer)
        remarks = sum(1 for row in rows if row.remarks.strip())
        return answered, len(yesno), remarks

    @staticmethod
    def section_rows(sheet):
        """The recorded answers grouped by official section (read-only view)."""
        from donors.interview import SECTIONS

        answers = sheet.answer_map()
        groups = []
        for section in SECTIONS:
            rows = []
            for item in section.items:
                answer = answers.get(item.key)
                rows.append({
                    "item": item,
                    "answer": answer.answer if answer else "",
                    "answer_display": (answer.get_answer_display()
                                       if answer and answer.answer else ""),
                    "remarks": answer.remarks if answer else "",
                })
            groups.append({"section": section, "rows": rows})
        return groups

    @staticmethod
    def save(sheet, *, data, actor, request=None):
        """Persist one form submission, including the completion state."""
        from audit import services as audit
        from donors.models import DonorInterviewSheet, InterviewAnswer

        was_completed = sheet.is_completed
        completing = bool(data.get("mark_complete"))
        before = {"status": sheet.status, "cue_choice": sheet.cue_choice}
        with transaction.atomic():
            # Only keys actually submitted are applied: a partial save (an API
            # caller, a script) must not blank fields the form did not send -
            # the pre-filled address would otherwise disappear on every save.
            # Values are stored exactly as they print: one case for the working
            # form, the preview and the PDF.
            for name in SHEET_TEXT_FIELDS:
                if name in data:
                    setattr(sheet, name, uppercase(data.get(name)))
            for name in SHEET_DATE_FIELDS:
                if name in data:
                    setattr(sheet, name, data.get(name) or None)
            if "cue_choice" in data:
                sheet.cue_choice = data.get("cue_choice") or ""
            if completing and not was_completed:
                sheet.status = DonorInterviewSheet.Status.COMPLETED
                sheet.completed_at = timezone.now()
                sheet.completed_by = actor
            elif not completing and was_completed:
                sheet.status = DonorInterviewSheet.Status.DRAFT
                sheet.completed_at = None
                sheet.completed_by = None
            sheet.save()
            for item in ALL_ITEMS:
                InterviewAnswer.objects.update_or_create(
                    sheet=sheet, item_key=item.key,
                    defaults={"answer": data.get(f"answer_{item.key}") or "",
                              "remarks": (data.get(f"remarks_{item.key}") or "").strip()})

            if sheet.is_completed and not was_completed:
                action, verb = "INTERVIEW_SHEET_COMPLETED", "completed and signed"
            elif was_completed and not sheet.is_completed:
                action, verb = "INTERVIEW_SHEET_REOPENED", "reopened for amendment"
            elif was_completed:
                action, verb = "INTERVIEW_SHEET_AMENDED", "amended after signing"
            else:
                action, verb = "INTERVIEW_SHEET_UPDATED", "saved as a draft"
            answered, total, remarks = InterviewSheetService.progress(sheet)
            audit.log(request, user=actor, action=action, module="donors", obj=sheet,
                      before=before, after={"status": sheet.status, "cue_choice": sheet.cue_choice},
                      description=(f"Interview sheet for donor {sheet.donor.donor_code} {verb}; "
                                   f"{answered}/{total} YES/NO items answered, "
                                   f"{remarks} remark(s)."))
        return sheet
