"""The printable PRC / DOH Blood Donor Interview Sheet.

``build_interview_sheet_spec`` turns one ``DonorInterviewSheet`` into a
``FormDocumentSpec`` for :mod:`core.documents`, so the print preview and the
downloaded PDF are typeset from the same data and can never diverge.

The spec is a *record*: it prints the answers that exist and leaves the rest
blank. An unanswered item stays an empty YES/NO pair, never an implied "No",
and the spec contains no clinical interpretation (agents.md rule 1).
"""
from core.documents import (DocumentColumn, FormDocumentSpec, FormField, FormRow,
                            FormSection)
from core.text import uppercase
from donors.interview import SECTIONS


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
