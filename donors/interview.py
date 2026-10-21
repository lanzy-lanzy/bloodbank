"""The PRC / DOH "Blood Donor Interview Sheet" questionnaire.

This module is the single source of truth for *which* items the printed sheet
contains and in which order. The wording is transcribed from the official
Philippine Red Cross / Department of Health form; it is the document's text,
not a clinical decision rule. Nothing here evaluates a donor: an answer is
recorded, never interpreted (see agents.md rule 1). Eligibility stays with
staff and the configured screening tables in ``donors.models``.

Item keys (``Q01`` … ``Q37``) are stable and are what an ``InterviewAnswer``
row stores, so re-wording an item later never detaches the answers already on
file. Numbering follows the paper form, including the two date-answer items
(last childbirth, last menstrual period) that have no YES/NO column.

The sign-up form asks this same questionnaire: every YES/NO item has one
declaration field on ``accounts.RegistrationInterview``, named after the item
(``Q01`` -> ``q01``). The mapping is derived from this module (``SIGNUP_FIELDS``)
instead of being typed twice, so a form answer always lands on its own item and
nothing is inferred: a blank stays blank.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class InterviewItem:
    """One numbered line of the medical history questionnaire."""

    key: str
    number: int
    text: str
    # "YESNO" renders the YES / NO / REMARKS columns of the paper form;
    # "DATE" is a date-only line (the form prints "DATE:" in the remarks box).
    kind: str = "YESNO"


@dataclass(frozen=True)
class InterviewSection:
    """A titled group of items. An empty title means "no sub-heading"."""

    key: str
    title: str
    items: tuple = ()
    note: str = ""


SECTIONS = (
    InterviewSection(
        key="general",
        title="",
        note=("Please read carefully and answer all relevant questions. "
              "Tick the appropriate answer; use Remarks for anything that needs context."),
        items=(
            InterviewItem("Q01", 1, "Do you feel well and healthy today?"),
            InterviewItem("Q02", 2, "Have you ever been refused as a blood donor or told "
                                    "not to donate blood for any reasons?"),
            InterviewItem("Q03", 3, "Are you giving blood only because you want to be tested "
                                    "for HIV or the AIDS virus or Hepatitis virus?"),
            InterviewItem("Q04", 4, "Are you aware that an HIV / Hepatitis infected person can "
                                    "still transmit the virus despite a negative HIV / "
                                    "Hepatitis test?"),
            InterviewItem("Q05", 5, "Have you within the last 12 HOURS had taken liquor, beer "
                                    "or any drinks with alcohol?"),
            InterviewItem("Q06", 6, "In the last 3 DAYS have you taken aspirin?"),
            InterviewItem("Q07", 7, "In the past 4 WEEKS have you taken any medications "
                                    "and/or vaccinations?"),
            InterviewItem("Q08", 8, "In the past 3 MONTHS have you donated whole blood, "
                                    "platelets or plasma?"),
        ),
    ),
    InterviewSection(
        key="six_months",
        title="IN THE PAST 6 MONTHS HAVE YOU:",
        items=(
            InterviewItem("Q09", 9, "Been to any places in the Philippines or countries "
                                    "infected with ZIKA Virus?"),
            InterviewItem("Q10", 10, "Had sexual contact with a person who was confirmed to "
                                     "have ZIKA Virus infection?"),
            InterviewItem("Q11", 11, "Had sexual contact with a person who has been to any "
                                     "places in the Philippines or countries infected with "
                                     "ZIKA Virus?"),
        ),
    ),
    InterviewSection(
        key="twelve_months",
        title="IN THE PAST 12 MONTHS HAVE YOU:",
        items=(
            InterviewItem("Q12", 12, "Received blood, blood products and/or had tissue/organ "
                                     "transplant or graft?"),
            InterviewItem("Q13", 13, "Had surgical operation or dental extraction?"),
            InterviewItem("Q14", 14, "Had a tattoo applied, ear and body piercing, acupuncture, "
                                     "needle stick injury or accidental contact with blood?"),
            InterviewItem("Q15", 15, "Had sexual contact with high risks individuals or in "
                                     "exchange for material or monetary gain?"),
            InterviewItem("Q16", 16, "Engaged in unprotected, unsafe or casual sex?"),
            InterviewItem("Q17", 17, "Had jaundice/hepatitis/ personal contact with person who "
                                     "had hepatitis?"),
            InterviewItem("Q18", 18, "Been incarcerated, jailed or imprisoned?"),
            InterviewItem("Q19", 19, "Spent time or have relatives in the United Kingdom or "
                                     "Europe?"),
        ),
    ),
    InterviewSection(
        key="ever",
        title="HAVE YOU EVER:",
        items=(
            InterviewItem("Q20", 20, "Travelled or lived outside of your place of residence or "
                                     "outside the Philippines?"),
            InterviewItem("Q21", 21, "Taken prohibited drugs (orally, by nose, or by injection)?"),
            InterviewItem("Q22", 22, "Used clotting factor concentrates?"),
            InterviewItem("Q23", 23, "Had a positive test for the HIV virus, Hepatitis virus, "
                                     "Syphilis or Malaria?"),
            InterviewItem("Q24", 24, "Had Malaria or Hepatitis in the past?"),
            InterviewItem("Q25", 25, "Had or was treated for genital wart, syphilis, gonorrhea "
                                     "or other sexually transmitted diseases?"),
        ),
    ),
    InterviewSection(
        key="conditions",
        title="HAD ANY OF THE FOLLOWING:",
        items=(
            InterviewItem("Q26", 26, "Cancer, blood disease or bleeding disorder (haemophilia)?"),
            InterviewItem("Q27", 27, "Heart disease/surgery, rheumatic fever or chest pains?"),
            InterviewItem("Q28", 28, "Lung disease, tuberculosis or asthma?"),
            InterviewItem("Q29", 29, "Kidney disease, thyroid disease, diabetes, epilepsy?"),
            InterviewItem("Q30", 30, "Chicken pox and/or cold sores?"),
            InterviewItem("Q31", 31, "Any other chronic medical condition or surgical operations?"),
            InterviewItem("Q32", 32, "Have you recently had rash and/or fever? Was/were "
                                     "this/these also associated with arthralgia or arthritis "
                                     "or conjunctivitis?"),
        ),
    ),
    InterviewSection(
        key="female",
        title="FOR FEMALE DONORS ONLY:",
        note="Answered by female donors; leave blank when not applicable.",
        items=(
            InterviewItem("Q33", 33, "Are you currently pregnant or have you ever been pregnant?"),
            InterviewItem("Q34", 34, "When was your last childbirth?", kind="DATE"),
            InterviewItem("Q35", 35, "In the past 1 YEAR did you have a miscarriage or abortion?"),
            InterviewItem("Q36", 36, "Are you currently breastfeeding?"),
            InterviewItem("Q37", 37, "When was your last menstrual period?", kind="DATE"),
        ),
    ),
)

ALL_ITEMS = tuple(item for section in SECTIONS for item in section.items)
ITEM_BY_KEY = {item.key: item for item in ALL_ITEMS}
SECTION_BY_KEY = {item.key: section.key for section in SECTIONS for item in section.items}
FEMALE_SECTION = "female"

# One sign-up answer field per YES/NO item, named after the item key. The two
# DATE items (Q34, Q37) have no YES/NO column and are not asked at sign-up.
SIGNUP_FIELDS = tuple(item.key.lower() for item in ALL_ITEMS if item.kind == "YESNO")
FEMALE_KEYS = frozenset(item.key for section in SECTIONS if section.key == FEMALE_SECTION
                        for item in section.items)

# Fields on the generated sheet that the registration data cannot supply and
# staff complete at the clinic (used by the form, the service and the printer).
SHEET_TEXT_FIELDS = (
    "prc_donor_number",
    "doh_barcode",
    "civil_status",
    "nationality",
    "religion",
    "education",
    "occupation",
    "office_address",
    "address_no",
    "address_street",
    "address_barangay",
    "address_zip",
    "telephone_number",
    "id_school",
    "id_company",
    "id_prc",
    "id_drivers",
    "id_sss",
    "id_others",
    "cue_unit_serial",
    "cue_place_of_donation",
    "donor_signature",
    "interviewer_signature",
)

SHEET_DATE_FIELDS = (
    "cue_date_of_donation",
    "donor_signed_on",
    "interviewer_signed_on",
)
