"""The printable PRC / DOH Blood Donor Interview Sheet.

``build_interview_sheet_spec`` turns one ``DonorInterviewSheet`` into a
``FormDocumentSpec`` for :mod:`core.documents`, so the print preview and the
downloaded PDF are typeset from the same data and can never diverge.

The spec is a *record*: it prints the answers that exist and leaves the rest
blank. An unanswered item stays an empty YES/NO pair, never an implied "No",
and the spec contains no clinical interpretation (agents.md rule 1).
"""
from django.db.models import Q
from django.utils import timezone

from core.documents import (DocumentColumn, FormDocumentSpec, FormField, FormRow,
                            FormSection, build_document)
from core.text import uppercase
from donors.interview import SECTIONS

# Bounded row cap for the printable/PDF donor directory (Rule 8: a truncated
# listing must explicitly say it is truncated).
DONOR_LIST_LIMIT = 500

DONOR_DIRECTORY_COLUMNS = [
    "Donor Code",
    "Full Name",
    "Blood Type",
    "Contact",
    "Municipality",
    "Last Donation",
    "Status",
    "Eligibility",
    "Points",
]


def _date(value) -> str:
    return value.strftime("%d %b %Y") if value else ""


def _age(donor) -> str:
    # A pending registration may predate the date-of-birth requirement: print
    # a blank rather than fail, and never invent an age.
    return donor.age if donor.date_of_birth else ""


def _columns() -> tuple:
    # Explicit widths: the question column must keep the page, and the two tick
    # columns stay narrow like the paper form.
    return (
        DocumentColumn("#", align="center", width=4),
        DocumentColumn("Question", width=50),
        DocumentColumn("YES", align="center", width=6),
        DocumentColumn("NO", align="center", width=6),
        DocumentColumn("REMARKS", width=34),
    )


def _identity_section(sheet) -> FormSection:
    # Every printed value goes through ``uppercase``: the sheet is stored and
    # printed in one case, and a legacy mixed-case record still prints the way
    # the clinic files it. Dates, ages and counts are left as they are.
    donor = sheet.donor
    blood_type = donor.blood_type.code if donor.blood_type_id else "Not on file"
    return FormSection(
        kind="fields",
        title="Donor information",
        rows=(
            FormRow("Name", cells=(
                FormField("Surname, first name and middle name",
                          uppercase(donor.full_name)),)),
            FormRow("Birth date", cells=(
                FormField("Date of birth", _date(donor.date_of_birth), width=26),
                FormField("Age", _age(donor), width=8),
                FormField("Sex", uppercase(donor.get_sex_display()), width=16),
                FormField("Civil status", uppercase(sheet.get_civil_status_display()),
                          width=18),
                FormField("Blood type", uppercase(blood_type), width=15),
            )),
            FormRow("Address", cells=(
                FormField("House / lot no.", uppercase(sheet.address_no), width=16),
                FormField("Street", uppercase(sheet.address_street), width=34),
                FormField("Barangay", uppercase(sheet.address_barangay)),
                FormField("Zip code", uppercase(sheet.address_zip), width=12),
            )),
            FormRow("Contact", cells=(
                FormField("Telephone no.", uppercase(sheet.telephone_number), width=28),
                FormField("Mobile no.", uppercase(donor.contact_number)),
                FormField("Email address", uppercase(donor.email)),
            )),
            FormRow("Other", cells=(
                FormField("Nationality", uppercase(sheet.nationality)),
                FormField("Religion", uppercase(sheet.religion)),
                FormField("Education", uppercase(sheet.education)),
                FormField("Occupation", uppercase(sheet.occupation)),
            )),
            FormRow("Office", cells=(
                FormField("Office address", uppercase(sheet.office_address)),)),
            FormRow("IDs", cells=(
                FormField("School ID", uppercase(sheet.id_school)),
                FormField("Company ID", uppercase(sheet.id_company)),
                FormField("PRC ID", uppercase(sheet.id_prc)),
                FormField("Driver's licence", uppercase(sheet.id_drivers)),
                FormField("SSS / GSIS / BIR", uppercase(sheet.id_sss)),
                FormField("Others", uppercase(sheet.id_others)),
            )),
        ),
    )


def _questionnaire_section(sheet, answers=None) -> FormSection:
    """One row per official item; group headings follow the paper form.

    ``answers`` defaults to the rows on file; the pending preview passes the
    in-memory declaration instead (a transient sheet has no rows to query).
    """
    answers = sheet.answer_map() if answers is None else answers
    items = []
    for section in SECTIONS:
        heading = section.title
        if heading and section.note:
            heading = f"{heading} {section.note}"
        for index, item in enumerate(section.items):
            row = answers.get(item.key)
            answer = row.answer if row else ""
            remarks = (row.remarks if row else "").strip()
            if not remarks and item.kind == "DATE":
                remarks = "DATE:"
            # "box" cells print a drawn tick box: an unanswered item still
            # prints an empty box the interviewer can tick by hand.
            cells = (
                (f"{item.number}.", "center"),
                (item.text, "left"),
                ("X" if answer == "YES" else "", "box"),
                ("X" if answer == "NO" else "", "box"),
                (remarks, "left"),
            )
            items.append((heading if index == 0 else "", cells))
    return FormSection(
        kind="table",
        title="Medical history questionnaire",
        note=SECTIONS[0].note,
        columns=_columns(),
        items=tuple(items),
    )


def _cue_panel(sheet) -> dict:
    """The Confidential Unit Exclusion block, with the declaration ticked."""
    return {
        "title": "Confidential Unit Exclusion (CUE)",
        "body": [
            "Read the statement below and mark the one that applies to this donation.",
            "Your answer is confidential: it stays with the donation record and is "
            "not shown to the recipient.",
        ],
        "question": "I believe that my blood is:",
        "options": [
            ("SAFE for transfusion", sheet.cue_choice == "SAFE"),
            ("NOT SAFE for transfusion", sheet.cue_choice == "NOT_SAFE"),
        ],
        "fields": [
            ("Unit serial no.", uppercase(sheet.cue_unit_serial)),
            ("Date of donation", _date(sheet.cue_date_of_donation)),
            ("Place of donation", uppercase(sheet.cue_place_of_donation)),
        ],
        "footnote": "",
    }


def build_interview_sheet_spec(sheet, *, answers=None, preview=False,
                               preview_note="") -> FormDocumentSpec:
    """The print preview / PDF spec for ``sheet``'s donor.

    ``answers`` lets a preview pass the in-memory declaration (a transient
    sheet has no rows to query). ``preview=True`` swaps the draft banner for a
    clear "not a record yet" one and says so in the subtitle, so a preview can
    never be mistaken for a signed sheet. ``preview_note`` replaces the default
    pending-registration explanation when the caller is previewing something
    else (an application that has not been submitted yet).

    The sign-up form asks this sheet's own questionnaire, so a pre-filled
    answer always sits on its own item; there is no second listing to print.
    """
    donor = sheet.donor
    sections = [_identity_section(sheet), _questionnaire_section(sheet, answers)]
    notes = []
    if preview:
        notes.append(preview_note or
                     "PREVIEW - built from a pending registration. The donor record "
                     "and this interview sheet are created only when an administrator "
                     "approves the registration.")
    elif not sheet.is_completed:
        notes.append("DRAFT - this interview sheet has not been completed and signed yet.")
    notes.append("This sheet records the donor's declarations. Eligibility is decided by "
                 "the blood bank staff after the interview and the configured screening "
                 "checks, not by this form.")
    return FormDocumentSpec(
        title="Blood Donor Interview Sheet",
        subtitle=("Preview - not yet a donor record"
                  if preview else "Medical history and confidentiality declaration"),
        reference=uppercase(donor.donor_code),
        header=(
            FormField("Blood donor no.", uppercase(sheet.prc_donor_number), width=50),
            FormField("DOH NBBets barcode", uppercase(sheet.doh_barcode), width=50),
        ),
        sections=tuple(sections),
        cue=_cue_panel(sheet),
        # A blank line until the donor actually signs: the spec must never
        # print a name on the signature rule as if the sheet were signed.
        signatures=(
            ("Donor", uppercase(sheet.donor_signature), "Date",
             _date(sheet.donor_signed_on)),
            ("Interviewer", uppercase(sheet.interviewer_signature), "Date",
             _date(sheet.interviewer_signed_on)),
        ),
        notes=notes,
        footer_note=("Confidential - contains personal health information. "
                     "Keep with the donor's record."),
        pdf_filename=f"blood-donor-interview-{donor.donor_code}.pdf",
    )


def filter_donor_queryset(params):
    """Apply DonorListView's filter parameters to the Donor queryset.

    Shared by the HTML list, the print preview, the PDF export, and the CSV
    export so every output reflects the exact same filtered set of donors.
    """
    from donors.models import Donor
    from donors.services import DonorEligibilityService

    qs = Donor.objects.select_related("blood_type")
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(donor_code__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
            | Q(middle_name__icontains=q) | Q(contact_number__icontains=q) | Q(email__icontains=q)
        )
    for key, field in (("status", "status"), ("blood_type", "blood_type_id"), ("municipality", "municipality")):
        value = params.get(key)
        if value:
            qs = qs.filter(**{f"{field}__icontains" if field == "municipality" else field: value})
    last_from, last_to = params.get("last_donation_from"), params.get("last_donation_to")
    if last_from or last_to:
        donations_filter = Q(donations__status__in=["COLLECTED", "RELEASED"])
        if last_from:
            donations_filter &= Q(donations__donation_date__gte=last_from)
        if last_to:
            donations_filter &= Q(donations__donation_date__lte=last_to)
        qs = qs.filter(donations_filter).distinct()
    eligibility = params.get("eligibility") or ""
    if eligibility:
        ids = [d.pk for d in qs[:DONOR_LIST_LIMIT]
               if DonorEligibilityService.evaluate(d).status == eligibility]
        qs = qs.filter(pk__in=ids)
    return qs.order_by("last_name", "first_name")


def _active_donor_scope(params) -> str:
    """Human-readable summary of the active filters for the printed header."""
    from donors.models import Donor
    from inventory.models import BloodType

    parts = []
    if (params.get("q") or "").strip():
        parts.append(f"Search: {params['q'].strip()}")
    if params.get("status"):
        status_map = dict(Donor.Status.choices)
        parts.append(f"Status: {status_map.get(params['status'], params['status'])}")
    if params.get("blood_type"):
        bt = BloodType.objects.filter(pk=params["blood_type"]).first()
        parts.append(f"Blood type: {bt.code if bt else params['blood_type']}")
    if params.get("municipality"):
        parts.append(f"Municipality: {params['municipality']}")
    if params.get("eligibility"):
        parts.append(f"Eligibility: {params['eligibility'].replace('_', ' ').title()}")
    if params.get("last_donation_from") or params.get("last_donation_to"):
        parts.append(
            f"Last donation: {params.get('last_donation_from') or '...'} to "
            f"{params.get('last_donation_to') or '...'}"
        )
    return "; ".join(parts) if parts else "All registered donors"


def donor_directory_rows(donors) -> tuple[list, dict]:
    """Format donor instances into table rows and real summary counters."""
    from donors.services import DonorEligibilityService

    rows = []
    counts = {"active": 0, "eligible": 0, "deferred_or_ineligible": 0}
    for d in donors:
        elig = DonorEligibilityService.evaluate(d)
        if d.status == "ACTIVE":
            counts["active"] += 1
        if elig.status == "ELIGIBLE":
            counts["eligible"] += 1
        elif elig.status in ("DEFERRED", "NOT_ELIGIBLE"):
            counts["deferred_or_ineligible"] += 1
        last_don = d.last_donation
        rows.append([
            uppercase(d.donor_code),
            uppercase(d.full_name),
            d.blood_type.code if d.blood_type_id else "-",
            d.contact_number or d.email or "-",
            uppercase(d.municipality) if d.municipality else "-",
            last_don.donation_date.strftime("%d %b %Y") if last_don else "-",
            d.get_status_display(),
            elig.status.replace("_", " ").title(),
            str(d.points_balance),
        ])
    return rows, counts


def build_donor_directory_spec(request):
    """Build the formal DocumentSpec for the Donor Registry preview and PDF."""
    qs = filter_donor_queryset(request.GET)
    total = qs.count()
    donors = list(qs[:DONOR_LIST_LIMIT])
    rows, counts = donor_directory_rows(donors)

    generated = timezone.localtime()
    user = request.user
    full_name = (getattr(user, "get_full_name", lambda: "")() or "").strip()
    prepared_by = f"{full_name} ({user.get_username()})" if full_name else user.get_username()
    scope = _active_donor_scope(request.GET)

    notes = [
        f"Scope applied - {scope}.",
        "Eligibility status is evaluated live against configured blood bank rules.",
    ]
    if total > DONOR_LIST_LIMIT:
        notes.append(
            f"Listing truncated to the first {DONOR_LIST_LIMIT:,} of {total:,} matching donors. "
            "Narrow the filters or export CSV for the complete set."
        )

    return build_document(
        title="Donor Registry",
        doc_type="DIRECTORY",
        subtitle="Registered blood donors, blood group, contact details, status and current eligibility.",
        columns=DONOR_DIRECTORY_COLUMNS,
        rows=rows,
        meta=[
            ("Generated on", generated.strftime("%d %b %Y, %H:%M")),
            ("Generated by", prepared_by),
            ("Matched donors", f"{total:,}"),
            ("Scope", scope),
        ],
        summary=[
            ("Total Matched", f"{total:,}"),
            ("Active (Shown)", f"{counts['active']:,}"),
            ("Eligible (Shown)", f"{counts['eligible']:,}"),
            ("Deferred / Ineligible", f"{counts['deferred_or_ineligible']:,}"),
        ],
        landscape=True,
        notes=notes,
        prepared_by=prepared_by,
        pdf_filename=f"donors_{generated:%Y%m%d_%H%M}.pdf",
    )
