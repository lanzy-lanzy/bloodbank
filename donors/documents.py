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
        title="I. PERSONAL DATA",
        subtitle="(to be filled up by the donor):",
        rows=(
            FormRow("NAME:", cells=(
                FormField("Surname, first name and middle name",
                          uppercase(donor.full_name)),)),
            FormRow("", cells=(
                FormField("Date of birth", _date(donor.date_of_birth), width=26),
                FormField("Age", _age(donor), width=8),
                FormField("Civil status", uppercase(sheet.get_civil_status_display()),
                          width=22),
                FormField("Sex", uppercase(donor.get_sex_display()), width=20),
                FormField("Blood type", uppercase(blood_type), width=24),
            )),
            FormRow("PERMANENT ADDRESS:", cells=(
                FormField("No.", uppercase(sheet.address_no), width=10),
                FormField("Street", uppercase(sheet.address_street), width=26),
                FormField("Barangay", uppercase(sheet.address_barangay), width=14),
                FormField("Town/Municipality", uppercase(donor.municipality), width=14),
                FormField("Province/City", uppercase(donor.province), width=11),
                FormField("Zip code", uppercase(sheet.address_zip), width=8),
            )),
            FormRow("OFFICE ADDRESS", cells=(
                FormField("Office address", uppercase(sheet.office_address)),)),
            FormRow("", cells=(
                FormField("Nationality", uppercase(sheet.nationality), width=25),
                FormField("Religion", uppercase(sheet.religion), width=25),
                FormField("Education", uppercase(sheet.education), width=25),
                FormField("Occupation", uppercase(sheet.occupation), width=25),
            )),
            FormRow("CONTACT No.:", cells=(
                FormField("Telephone no.", uppercase(sheet.telephone_number), width=27),
                FormField("Mobile no.", uppercase(donor.contact_number), width=28),
                FormField("Email address", uppercase(donor.email), width=28),
            )),
            FormRow("IDENTIFICATION No.:", cells=(
                FormField("School", uppercase(sheet.id_school)),
                FormField("Company", uppercase(sheet.id_company)),
                FormField("PRC", uppercase(sheet.id_prc)),
                FormField("Driver's", uppercase(sheet.id_drivers)),
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
        title="II. MEDICAL HISTORY",
        subtitle="(Please read carefully and answer all relevant questions. Tick the appropriate answer.)",
        note="",
        columns=_columns(),
        items=tuple(items),
    )


def _cue_panel(sheet) -> dict:
    """The Confidential Unit Exclusion block, with the declaration ticked."""
    return {
        "title": "CONFIDENTIAL UNIT EXCLUSION (CUE)",
        "body": [
            "Please MARK one of the boxes below. If at one point DURING or AFTER donating blood "
            "you are unsure of your initial answer, please inform our Blood Service Staff immediately. "
            "If you have already left the blood donation venue, contact the blood bank office nearest you.",
            "MARK ONE BOX ONLY. YOUR RESPONSE WILL BE STRICTLY CONFIDENTIAL.",
        ],
        "question": "I BELIEVE THAT MY BLOOD IS:",
        "options": [
            ("SAFE for transfusion", sheet.cue_choice == "SAFE"),
            ("NOT SAFE for transfusion", sheet.cue_choice == "NOT_SAFE"),
        ],
        "fields": [
            ("BBIS DONATION ID", uppercase(sheet.cue_unit_serial)),
            ("DATE OF DONATION", _date(sheet.cue_date_of_donation)),
            ("PLACE OF DONATION", uppercase(sheet.cue_place_of_donation)),
        ],
        "footnote": "*THANK YOU FOR DONATING YOUR BLOOD AND FOR HELPING MAINTAIN A SAFE BLOOD SUPPLY*",
    }


def _back_page(sheet) -> dict:
    """Data pre-filled onto Page 2 (Sections III-VI) of the official form."""
    donor = sheet.donor
    return {
        "donor_signature": uppercase(sheet.donor_signature),
        "donor_signed_on": _date(sheet.donor_signed_on),
        "interviewer_signature": uppercase(sheet.interviewer_signature),
        "interviewer_signed_on": _date(sheet.interviewer_signed_on),
        "blood_type": uppercase(donor.blood_type.code) if donor.blood_type_id else "",
        "donation_id": uppercase(sheet.cue_unit_serial),
        "form_code": "PRC-NBS DONOR FORM 321-E Revised Sep2025",
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
        title="BLOOD DONOR INTERVIEW SHEET",
        subtitle=("Preview - not yet a donor record"
                  if preview else ""),
        reference=uppercase(donor.donor_code),
        header=(
            FormField("Blood donor no.", uppercase(sheet.prc_donor_number), width=50),
            FormField("DOH NBBNets BARCODE", uppercase(sheet.doh_barcode), width=50),
        ),
        sections=tuple(sections),
        cue=_cue_panel(sheet),
        back_page=_back_page(sheet),
        # Signatures live on Page 2 (Sections III & IV) when back_page is set.
        signatures=(),
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
