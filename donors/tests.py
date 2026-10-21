"""Donor domain: eligibility engine (fail-safe), soft deletion, data scoping."""
from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.testing import (make_blood_type, make_component, make_donor, make_donation,
                          make_bag, make_user, set_rules)
from donors.forms import DonorInterviewSheetForm, DonorSelfProfileForm
from donors.interview import ALL_ITEMS, SECTION_BY_KEY
from donors.models import Donor, DonorInterviewSheet
from donors.services import DonorEligibilityService, InterviewSheetService


class DonorContactValidationTests(TestCase):
    """contact_number is required and must be a valid Philippine mobile so SMS reaches the donor."""

    def test_missing_contact_rejected(self):
        form = DonorSelfProfileForm(data={"email": "", "address": "", "municipality": "",
                                          "province": "", "emergency_contact_name": "",
                                          "emergency_contact_phone": "", "contact_number": ""})
        self.assertIn("contact_number", form.errors)

    def test_invalid_mobile_rejected(self):
        form = DonorSelfProfileForm(data={"contact_number": "12345"})
        self.assertIn("contact_number", form.errors)

    def test_valid_mobile_with_dashes_accepted(self):
        form = DonorSelfProfileForm(data={"contact_number": "0917-123-4567"})
        self.assertTrue(form.is_valid(), form.errors)


class EligibilityRuleTests(TestCase):
    def setUp(self):
        self.bt = make_blood_type("O", "POS")
        self.donor = make_donor(blood_type=self.bt)

    def test_eligible_when_rules_configured_and_satisfied(self):
        set_rules(min_age_years=18, max_age_years=65, min_donation_interval_days=60)
        result = DonorEligibilityService.evaluate(self.donor)
        self.assertEqual(result.status, "ELIGIBLE")
        self.assertTrue(result.is_eligible)

    def test_interval_not_configured_fails_safe_to_staff_review(self):
        # No min_donation_interval_days setting at all + a past donation.
        make_donation(self.donor, status="RELEASED",
                      donation_date=timezone.localdate() - timedelta(days=400))
        result = DonorEligibilityService.evaluate(self.donor)
        self.assertEqual(result.status, "REQUIRES_STAFF_REVIEW")

    def test_recent_donation_defers_until_interval(self):
        set_rules(min_age_years=18, max_age_years=65, min_donation_interval_days=60)
        last = timezone.localdate() - timedelta(days=10)
        make_donation(self.donor, status="RELEASED", donation_date=last)
        result = DonorEligibilityService.evaluate(self.donor)
        self.assertEqual(result.status, "DEFERRED")
        self.assertEqual(result.next_eligible_date, last + timedelta(days=60))

    def test_perm_deferred_never_eligible(self):
        donor = make_donor(blood_type=self.bt, status=Donor.Status.PERM_DEFERRED,
                           deferral_reason="test")
        set_rules(min_age_years=18, max_age_years=65, min_donation_interval_days=60)
        result = DonorEligibilityService.evaluate(donor)
        self.assertEqual(result.status, "NOT_ELIGIBLE")

    def test_active_temp_deferred_within_window(self):
        donor = make_donor(blood_type=self.bt, status=Donor.Status.TEMP_DEFERRED,
                           deferred_until=timezone.localdate() + timedelta(days=30))
        set_rules(min_age_years=18, max_age_years=65, min_donation_interval_days=60)
        result = DonorEligibilityService.evaluate(donor)
        self.assertEqual(result.status, "DEFERRED")

    def test_rolling_year_cap(self):
        set_rules(min_age_years=18, max_age_years=65, min_donation_interval_days=1,
                  max_donations_per_year=2)
        for days_back in (5, 10, 15):
            make_donation(self.donor, status="COLLECTED",
                          donation_date=timezone.localdate() - timedelta(days=days_back))
        result = DonorEligibilityService.evaluate(self.donor)
        self.assertEqual(result.status, "NOT_ELIGIBLE")

    def test_age_bounds_use_configured_values(self):
        set_rules(min_age_years=18, max_age_years=65, min_donation_interval_days=60)
        young = make_donor(blood_type=self.bt, age=16)
        self.assertEqual(DonorEligibilityService.evaluate(young).status, "NOT_ELIGIBLE")


class DonorRepositoryTests(TestCase):
    def test_soft_delete_hides_from_default_manager(self):
        donor = make_donor()
        donor.soft_delete()
        self.assertNotIn(donor, Donor.objects.all())
        self.assertIn(donor, Donor.all_objects.all())
        self.assertTrue(donor.is_deleted and donor.deleted_at)

    def test_codes_auto_assigned(self):
        donor = make_donor()
        self.assertTrue(donor.donor_code.startswith("DON-"))


class DonorAccessControlTests(TestCase):
    def setUp(self):
        from django.test import Client
        self.client = Client()
        self.bt = make_blood_type("A", "POS")
        self.staff = make_user("sec-staff", role="STAFF")
        donor_user = make_user("sec-donor", role="DONOR")
        self.my_donor = make_donor(user=donor_user, blood_type=self.bt)
        self.other_donor = make_donor(blood_type=self.bt)

    def test_donor_cannot_see_other_donor(self):
        self.client.login(username="sec-donor", password="Test12345!")
        resp = self.client.get(f"/donors/{self.other_donor.pk}/")
        self.assertIn(resp.status_code, (403, 302, 404))

    def test_donor_cannot_open_staff_directory(self):
        self.client.login(username="sec-donor", password="Test12345!")
        resp = self.client.get("/donors/")
        self.assertIn(resp.status_code, (403, 302))

    def test_staff_can_open_directory(self):
        self.client.login(username="sec-staff", password="Test12345!")
        resp = self.client.get("/donors/")
        self.assertEqual(resp.status_code, 200)


# --- Donor interview sheet ---------------------------------------------------------
def sheet_post_data(skip_female=True, **overrides):
    """A complete POST body for the interview sheet (all items answered "NO")."""
    data = {}
    for item in ALL_ITEMS:
        if item.kind != "YESNO":
            continue
        if skip_female and SECTION_BY_KEY[item.key] == "female":
            continue
        data[f"answer_{item.key}"] = "NO"
    data.update({
        "cue_choice": "SAFE",
        "donor_signature": "Test Donor",
        "donor_signed_on": "2026-10-20",
        "interviewer_signature": "Sheet Staff",
        "interviewer_signed_on": "2026-10-20",
        "mark_complete": "on",
    })
    data.update(overrides)
    return data


class InterviewSheetGenerationTests(TestCase):
    def setUp(self):
        self.donor = make_donor(blood_type=make_blood_type("B", "NEG"),
                                first_name="Ana", last_name="Reyes",
                                address="12 Mabini St., Dipolog City")

    def test_generates_one_answer_per_item_and_is_idempotent(self):
        sheet = InterviewSheetService.ensure_for_donor(self.donor)
        self.assertEqual(sheet.answers.count(), len(ALL_ITEMS))
        self.assertEqual(sheet.address_street, "12 MABINI ST., DIPOLOG CITY")
        self.assertEqual(sheet.status, "DRAFT")
        again = InterviewSheetService.ensure_for_donor(self.donor)
        self.assertEqual(again.pk, sheet.pk)
        self.assertEqual(self.donor.interview_sheet.answers.count(), len(ALL_ITEMS))

    def test_generation_is_audited(self):
        from audit.models import AuditLog
        InterviewSheetService.ensure_for_donor(self.donor)
        self.assertTrue(AuditLog.objects.filter(action="INTERVIEW_SHEET_CREATED").exists())

    def test_unanswered_item_stays_blank(self):
        sheet = InterviewSheetService.ensure_for_donor(self.donor)
        self.assertEqual(sheet.answer_for("Q16"), "")

    def test_address_adds_the_registered_municipality_and_province(self):
        donor = make_donor(blood_type=make_blood_type("A", "POS"),
                           address="7 Rizal St.", municipality="Tambulig",
                           province="Zamboanga del Sur")
        sheet = InterviewSheetService.ensure_for_donor(donor)
        self.assertEqual(sheet.address_street,
                         "7 RIZAL ST., TAMBULIG, ZAMBOANGA DEL SUR")

    def test_address_does_not_repeat_a_part_already_in_the_line(self):
        self.donor.municipality = "Dipolog City"  # already inside self.donor.address
        self.donor.province = "Zamboanga del Norte"
        self.donor.save(update_fields=["municipality", "province"])
        sheet = InterviewSheetService.ensure_for_donor(self.donor)
        self.assertEqual(sheet.address_street,
                         "12 MABINI ST., DIPOLOG CITY, ZAMBOANGA DEL NORTE")


class InterviewSheetPrefillTests(TestCase):
    def _registration(self, username, **interview):
        from accounts.models import RegistrationInterview, RegistrationRequest
        applicant = make_user(username, role="DONOR")
        reg = RegistrationRequest.objects.create(
            first_name="Ana", last_name="Reyes", username=username,
            email=f"{username}@example.test", phone="09171234567", role="DONOR",
            date_of_birth=date(1995, 5, 5), user=applicant)
        RegistrationInterview.objects.create(registration=reg, **interview)
        return reg

    def test_every_answer_is_copied_to_its_own_item(self):
        reg = self._registration("prefill-a", q01=True, q02=False, q07=True)
        donor = make_donor(blood_type=make_blood_type("B", "NEG"))
        sheet = InterviewSheetService.ensure_for_donor(donor, registration=reg)
        self.assertEqual(sheet.answer_for("Q01"), "YES")
        self.assertEqual(sheet.answer_for("Q02"), "NO")
        self.assertEqual(sheet.answer_for("Q07"), "YES")
        self.assertEqual(sheet.answer_for("Q12"), "")     # unanswered stays blank
        self.assertEqual(sheet.answer_for("Q23"), "")     # unanswered stays blank

    def test_female_block_is_not_asked_of_other_donors(self):
        reg = self._registration("prefill-male", q33=False, q36=False)
        donor = make_donor(blood_type=make_blood_type("B", "NEG"))  # MALE
        sheet = InterviewSheetService.ensure_for_donor(donor, registration=reg)
        self.assertEqual(sheet.answer_for("Q33"), "")
        self.assertEqual(sheet.answer_for("Q36"), "")

    def test_female_block_copies_both_answers_for_a_female_donor(self):
        donor = make_donor(blood_type=make_blood_type("B", "NEG"))
        donor.sex = "FEMALE"
        donor.save(update_fields=["sex"])

        negative = self._registration("prefill-female-no", q33=False, q35=False, q36=False)
        sheet = InterviewSheetService.ensure_for_donor(donor, registration=negative)
        self.assertEqual(sheet.answer_for("Q33"), "NO")
        self.assertEqual(sheet.answer_for("Q35"), "NO")
        self.assertEqual(sheet.answer_for("Q36"), "NO")

        positive = self._registration("prefill-female-yes", q33=True)
        other = make_donor(blood_type=make_blood_type("B", "NEG"))
        other.sex = "FEMALE"
        other.save(update_fields=["sex"])
        sheet = InterviewSheetService.ensure_for_donor(other, registration=positive)
        self.assertEqual(sheet.answer_for("Q33"), "YES")


class ApplicationPreviewTests(TestCase):
    """The interview sheet an application *will* create, built in memory.

    The applicant previews it before submitting, so it must match the record
    approval would generate and must never write anything.
    """

    def _application(self, **overrides):
        from accounts.models import RegistrationInterview
        data = {
            "role": "DONOR", "first_name": "Tina", "middle_name": "M",
            "last_name": "Test", "email": "tina@example.test", "phone": "09171234567",
            "sex": "FEMALE",
            "date_of_birth": date(1996, 2, 2), "address": "1 Test St",
            "municipality": "Zamboanga City", "province": "Zamboanga del Sur",
            "blood_type": None,
        }
        data.update(overrides)
        answers = {key: False for key in RegistrationInterview.QUESTION_FIELDS}
        answers.update({"q01": True, "declaration": True, "notes": ""})
        return data, answers

    def test_preview_is_transient_and_upper_cased(self):
        data, answers = self._application()
        sheet, rows = InterviewSheetService.preview_for_application(data, answers)
        self.assertIsNone(sheet.pk)
        self.assertIsNone(sheet.donor.pk)
        self.assertEqual(sheet.donor.donor_code, "PREVIEW")
        self.assertEqual(sheet.donor.full_name, "TINA M TEST")
        self.assertEqual(sheet.address_street,
                         "1 TEST ST, ZAMBOANGA CITY, ZAMBOANGA DEL SUR")
        self.assertFalse(sheet.is_completed)
        self.assertTrue(rows)
        self.assertTrue(all(row.pk is None for row in rows.values()))

    def test_declaration_pre_fills_the_items_it_answers(self):
        data, answers = self._application()
        _sheet, rows = InterviewSheetService.preview_for_application(data, answers)
        self.assertEqual(rows["Q01"].answer, "YES")
        self.assertEqual(rows["Q02"].answer, "NO")
        for key in ("Q07", "Q08", "Q12", "Q13", "Q14", "Q15", "Q23", "Q32",
                    "Q33", "Q35", "Q36"):
            self.assertEqual(rows[key].answer, "NO", key)
        # Every YES/NO item has its own field; the two DATE items have no box.
        self.assertEqual(len(rows), 35)
        self.assertNotIn("Q34", rows)
        self.assertNotIn("Q37", rows)

    def test_a_blank_answer_stays_blank(self):
        data, answers = self._application()
        answers["q08"] = None
        _sheet, rows = InterviewSheetService.preview_for_application(data, answers)
        self.assertNotIn("Q08", rows)

    def test_the_document_has_one_questionnaire_only(self):
        from donors.documents import build_interview_sheet_spec
        data, answers = self._application()
        sheet, rows = InterviewSheetService.preview_for_application(data, answers)
        spec = build_interview_sheet_spec(sheet, answers=rows, preview=True)
        self.assertEqual(len(spec.sections), 2)
        self.assertNotIn("Self-declaration recorded at sign-up",
                         [section.title for section in spec.sections])

    def test_preview_writes_nothing(self):
        from accounts.models import RegistrationInterview, RegistrationRequest
        from audit.models import AuditLog
        from donors.models import InterviewAnswer
        data, answers = self._application()
        InterviewSheetService.preview_for_application(data, answers)
        for model in (RegistrationRequest, RegistrationInterview, Donor,
                      DonorInterviewSheet, InterviewAnswer, AuditLog):
            self.assertEqual(model.objects.count(), 0, model.__name__)


class InterviewSheetFormTests(TestCase):
    def setUp(self):
        self.donor = make_donor(blood_type=make_blood_type("A", "NEG"))

    def test_non_female_donor_completes_without_the_female_block(self):
        form = DonorInterviewSheetForm(data=sheet_post_data(), donor=self.donor)
        self.assertTrue(form.is_valid(), form.errors)

    def test_completion_rejects_a_blank_item(self):
        data = sheet_post_data()
        data.pop("answer_Q05")
        form = DonorInterviewSheetForm(data=data, donor=self.donor)
        self.assertFalse(form.is_valid())
        self.assertIn("mark_complete", form.errors)

    def test_female_donor_must_answer_the_female_block(self):
        self.donor.sex = "FEMALE"
        form = DonorInterviewSheetForm(data=sheet_post_data(), donor=self.donor)
        self.assertFalse(form.is_valid())
        self.assertIn("mark_complete", form.errors)

    def test_draft_may_be_saved_blank(self):
        form = DonorInterviewSheetForm(data={}, donor=self.donor)
        self.assertTrue(form.is_valid(), form.errors)

    def test_completion_requires_cue_and_both_signatures(self):
        data = {k: v for k, v in sheet_post_data().items()
                if k not in ("cue_choice", "donor_signature", "donor_signed_on",
                             "interviewer_signature", "interviewer_signed_on")}
        form = DonorInterviewSheetForm(data=data, donor=self.donor)
        self.assertFalse(form.is_valid())
        for field in ("cue_choice", "donor_signature", "donor_signed_on",
                      "interviewer_signature", "interviewer_signed_on"):
            self.assertIn(field, form.errors)


class InterviewSheetSaveTests(TestCase):
    def setUp(self):
        self.donor = make_donor(blood_type=make_blood_type("O", "POS"))
        self.sheet = InterviewSheetService.ensure_for_donor(self.donor)
        self.staff = make_user("sheet-saver", role="STAFF")

    def _data(self, **overrides):
        """One complete submission, with real date objects like cleaned_data."""
        data = sheet_post_data(**overrides)
        data["donor_signed_on"] = date(2026, 10, 20)
        data["interviewer_signed_on"] = date(2026, 10, 20)
        data["mark_complete"] = True
        return data

    def test_save_stores_answers_and_marks_completion(self):
        data = self._data()
        data.update({"cue_choice": "NOT_SAFE", "civil_status": "SINGLE",
                     "address_zip": "7100"})
        InterviewSheetService.save(self.sheet, data=data, actor=self.staff)
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.status, DonorInterviewSheet.Status.COMPLETED)
        self.assertEqual(self.sheet.completed_by_id, self.staff.pk)
        self.assertEqual(self.sheet.cue_choice, "NOT_SAFE")
        self.assertEqual(self.sheet.civil_status, "SINGLE")
        self.assertEqual(self.sheet.address_zip, "7100")
        self.assertEqual(self.sheet.answer_for("Q12"), "NO")
        self.assertEqual(self.sheet.answer_for("Q33"), "")

    def test_saving_without_completion_reopens_a_signed_sheet(self):
        InterviewSheetService.save(self.sheet, data=self._data(), actor=self.staff)
        InterviewSheetService.save(self.sheet, data={}, actor=self.staff)
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.status, DonorInterviewSheet.Status.DRAFT)
        self.assertIsNone(self.sheet.completed_at)
        self.assertIsNone(self.sheet.completed_by_id)

    def test_partial_save_keeps_the_prefilled_header(self):
        # The registered address is written by ensure_for_donor(); a submission
        # that does not carry the field must not blank it.
        self.sheet.address_street = "1 Donor St"
        self.sheet.save(update_fields=["address_street"])
        InterviewSheetService.save(self.sheet, data={"answer_Q01": "NO"}, actor=self.staff)
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.address_street, "1 Donor St")
        self.assertEqual(self.sheet.answer_for("Q01"), "NO")

    def test_text_fields_are_stored_in_uppercase(self):
        # One case for the working form, the preview and the PDF: what staff
        # type is stored the way the document prints it.
        data = self._data(donor_signature="Test Donor",
                          interviewer_signature="Sheet Staff",
                          nationality="filipino", religion="roman catholic",
                          occupation="nurse")
        InterviewSheetService.save(self.sheet, data=data, actor=self.staff)
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.donor_signature, "TEST DONOR")
        self.assertEqual(self.sheet.interviewer_signature, "SHEET STAFF")
        self.assertEqual(self.sheet.nationality, "FILIPINO")
        self.assertEqual(self.sheet.religion, "ROMAN CATHOLIC")
        self.assertEqual(self.sheet.occupation, "NURSE")


class InterviewSheetViewTests(TestCase):
    def setUp(self):
        from django.test import Client
        self.client = Client()
        self.staff = make_user("sheet-staff", role="STAFF")
        donor_user = make_user("sheet-donor", role="DONOR")
        self.donor = make_donor(user=donor_user, blood_type=make_blood_type("O", "NEG"))
        self.other = make_donor(blood_type=make_blood_type("A", "POS"))

    def test_staff_page_renders_and_generates_the_sheet(self):
        self.client.login(username="sheet-staff", password="Test12345!")
        resp = self.client.get(reverse("donors:interview_sheet", args=[self.donor.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Medical history questionnaire")
        self.assertTrue(DonorInterviewSheet.objects.filter(donor=self.donor).exists())

    def test_staff_can_complete_the_sheet(self):
        self.client.login(username="sheet-staff", password="Test12345!")
        resp = self.client.post(
            reverse("donors:interview_sheet", args=[self.donor.pk]),
            sheet_post_data(), follow=True)
        self.assertEqual(resp.status_code, 200)
        sheet = DonorInterviewSheet.objects.get(donor=self.donor)
        self.assertEqual(sheet.status, DonorInterviewSheet.Status.COMPLETED)
        self.assertEqual(sheet.answer_for("Q01"), "NO")

    def test_donor_cannot_open_the_staff_sheet(self):
        self.client.login(username="sheet-donor", password="Test12345!")
        for name in ("donors:interview_sheet", "donors:interview_sheet_document",
                     "donors:interview_sheet_pdf"):
            resp = self.client.get(reverse(name, args=[self.other.pk]))
            self.assertIn(resp.status_code, (403, 302), name)

    def test_donor_reads_only_their_own_sheet(self):
        self.client.login(username="sheet-donor", password="Test12345!")
        resp = self.client.get(reverse("donors:my_interview_sheet"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.donor.donor_code)
        self.assertFalse(DonorInterviewSheet.objects.filter(donor=self.other).exists())

    def test_preview_and_pdf_are_the_same_document(self):
        self.client.login(username="sheet-staff", password="Test12345!")
        preview = self.client.get(
            reverse("donors:interview_sheet_document", args=[self.donor.pk]))
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "Blood Donor Interview Sheet")
        pdf = self.client.get(reverse("donors:interview_sheet_pdf", args=[self.donor.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF-"))


class InterviewSheetDocumentTests(TestCase):
    """The printed sheet is the record: identity data prints in upper case and
    a pending-registration preview says it is not a record yet."""

    def _sheet(self):
        donor = make_donor(blood_type=make_blood_type("O", "POS"),
                           first_name="ana", last_name="reyes",
                           address="12 mabini st.", municipality="dipolog city")
        sheet = InterviewSheetService.ensure_for_donor(donor)
        sheet.civil_status = "SINGLE"
        sheet.save(update_fields=["civil_status"])
        return sheet

    @staticmethod
    def _field_values(spec):
        return {cell.label: cell.value
                for section in spec.sections
                for row in section.rows
                for cell in row.cells}

    def test_identity_fields_print_in_uppercase(self):
        from donors.documents import build_interview_sheet_spec
        values = self._field_values(build_interview_sheet_spec(self._sheet()))
        self.assertEqual(values["Surname, first name and middle name"], "ANA REYES")
        self.assertEqual(values["Street"], "12 MABINI ST., DIPOLOG CITY")
        self.assertEqual(values["Sex"], "MALE")
        self.assertEqual(values["Civil status"], "SINGLE")
        self.assertEqual(values["Blood type"], "O+")

    def test_preview_spec_is_labelled_and_not_a_draft_record(self):
        from donors.documents import build_interview_sheet_spec
        spec = build_interview_sheet_spec(self._sheet(), preview=True)
        self.assertEqual(spec.subtitle, "Preview - not yet a donor record")
        self.assertTrue(any(note.startswith("PREVIEW") for note in spec.notes))
        self.assertFalse(any(note.startswith("DRAFT") for note in spec.notes))

    def test_preview_note_can_explain_an_unsaved_application(self):
        from donors.documents import build_interview_sheet_spec
        spec = build_interview_sheet_spec(
            self._sheet(), preview=True,
            preview_note="PREVIEW - built from your registration form.")
        self.assertEqual([note for note in spec.notes if note.startswith("PREVIEW")],
                         ["PREVIEW - built from your registration form."])

    def test_in_memory_answers_render_without_a_database_row(self):
        from donors.documents import build_interview_sheet_spec
        from donors.models import InterviewAnswer
        sheet = self._sheet()
        spec = build_interview_sheet_spec(
            sheet, answers={"Q01": InterviewAnswer(item_key="Q01", answer="YES")})
        table = spec.sections[1]
        q01 = [cells for heading, cells in table.items
               if cells[1][0].startswith("Do you feel well")]
        self.assertEqual(q01[0][2], ("X", "box"))   # YES ticked
        self.assertEqual(q01[0][3], ("", "box"))    # NO still an empty box
