"""Accounts: role model, login gating, user management permissions."""
from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import RegistrationInterview, RegistrationRequest, User
from core.testing import make_user
from notifications.models import NotificationTemplate

PASSWORD = "Test12345!"


def make_registration_templates():
    """Tests run on an unseeded DB, so the registration templates used by
    RegistrationService must exist inline."""
    NotificationTemplate.objects.bulk_create([
        NotificationTemplate(name="New registration", code="registration_submitted",
                             subject="New {{ role }} registration", body="{{ full_name }} signed up.",
                             channels="in_app"),
        NotificationTemplate(name="Registration approved", code="registration_approved",
                             subject="Welcome, {{ full_name }}",
                             body="Your {{ role }} registration was approved.",
                             channels="in_app,sms"),
        NotificationTemplate(name="Registration rejected", code="registration_rejected",
                             subject="Registration rejected",
                             body="Reason: {{ rejection_reason }}",
                             channels="in_app,sms"),
    ])


class RoleRoutingTests(TestCase):
    def _login_and_home(self, role, username):
        make_user(username, role=role)
        self.client.login(username=username, password="Test12345!")
        return self.client.get(reverse("core:dashboard"))

    def test_each_role_gets_a_dashboard(self):
        for role, name in (("ADMIN", "rr-admin"), ("STAFF", "rr-staff"),
                           ("DONOR", "rr-donor"), ("REQUESTER", "rr-req")):
            resp = self._login_and_home(role, name)
            self.assertEqual(resp.status_code, 200, f"{role} dashboard")

    def test_anonymous_dashboard_redirects_to_login(self):
        resp = self.client.get(reverse("core:dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url)


class UserManagementTests(TestCase):
    def setUp(self):
        self.admin = make_user("acc-admin", role="ADMIN")
        self.staff = make_user("acc-staff", role="STAFF")

    def test_admin_can_list_users(self):
        self.client.login(username="acc-admin", password="Test12345!")
        resp = self.client.get(reverse("accounts:user_list"))
        self.assertEqual(resp.status_code, 200)

    def test_staff_and_donor_cannot_list_users(self):
        make_user("acc-donor", role="DONOR")
        for name in ("acc-staff", "acc-donor"):
            self.client.login(username=name, password="Test12345!")
            resp = self.client.get(reverse("accounts:user_list"))
            self.assertIn(resp.status_code, (403, 302), name)

    def test_superuser_flag_not_required_for_admin_role(self):
        self.assertFalse(self.admin.is_superuser)
        self.client.login(username="acc-admin", password="Test12345!")
        resp = self.client.get(reverse("settings_app:index"))
        self.assertEqual(resp.status_code, 200)

    def test_password_is_not_stored_in_plaintext(self):
        u = User.objects.get(username="acc-admin")
        self.assertFalse(u.password.startswith("Test12345!"))
        self.assertTrue(u.check_password("Test12345!"))


class ProfilePhoneTests(TestCase):
    """Phone is required on the profile form so SMS notifications can reach the user."""

    def _form(self, phone):
        from accounts.forms import ProfileForm
        data = {"first_name": "R", "last_name": "Q", "email": "rq@example.test",
                "middle_name": "", "phone": phone}
        return ProfileForm(data)

    def test_missing_phone_rejected(self):
        self.assertIn("phone", self._form("").errors)

    def test_invalid_phone_rejected(self):
        self.assertIn("phone", self._form("landline").errors)

    def test_valid_ph_mobile_accepted(self):
        form = self._form("+63 917 123 4567")
        self.assertTrue(form.is_valid(), form.errors)


def interview_data(**overrides):
    """A complete, valid donor questionnaire: the printed sheet's own items."""
    data = {name: "no" for name in RegistrationInterview.QUESTION_FIELDS}
    data["q01"] = "yes"
    data.update({"declaration": "on", "interview_notes": ""})
    data.update(overrides)
    return data


def donor_form_data(**overrides):
    data = {"role": "DONOR", "first_name": "Tina", "middle_name": "M", "last_name": "Test",
            "username": "tina.test", "email": "tina@example.test", "phone": "09171234567",
            "sex": "FEMALE",
            "blood_type": "", "date_of_birth": "1996-02-02", "address": "1 Test St",
            "municipality": "Zamboanga City", "province": "Zamboanga del Sur",
            "organization_name": "", "password1": PASSWORD, "password2": PASSWORD}
    data.update(interview_data())
    data.update(overrides)
    return data



@override_settings(SMS_PROVIDER="mock", SMS_API_KEY="")
class RegistrationSubmitTests(TestCase):
    def setUp(self):
        make_registration_templates()
        self.admin = make_user("reg-admin", role="ADMIN")

    def _submit(self, **overrides):
        return self.client.post(reverse("public_registration:register"),
                                donor_form_data(**overrides))

    def test_public_page_renders_anonymously(self):
        resp = self.client.get(reverse("public_registration:register"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Create an account")

    def test_valid_submit_creates_inactive_user_and_pending_request(self):
        resp = self._submit()
        self.assertRedirects(resp, reverse("public_registration:register_done"))
        user = User.objects.get(username="tina.test")
        self.assertFalse(user.is_active)
        self.assertTrue(user.check_password(PASSWORD))
        self.assertEqual(user.role, "DONOR")
        reg = RegistrationRequest.objects.get(username="tina.test")
        self.assertEqual(reg.status, "PENDING")
        self.assertEqual(reg.user, user)
        from audit.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(action="REGISTRATION_SUBMITTED").exists())

    def test_admins_notified_on_submit(self):
        self._submit()
        self.assertTrue(self.admin.notifications.filter(
            template__code="registration_submitted", channel="in_app").exists())

    def test_elevated_role_rejected_by_form(self):
        for role in ("ADMIN", "STAFF"):
            resp = self._submit(role=role, username=f"evil-{role}", email=f"evil-{role}@example.test")
            self.assertEqual(resp.status_code, 200, role)
            self.assertFalse(RegistrationRequest.objects.filter(username=f"evil-{role}").exists())

    def test_service_level_role_guard(self):
        from accounts.services import RegistrationError, RegistrationService
        reg = RegistrationRequest(username="svc-evil", role="ADMIN", first_name="E",
                                  last_name="V", email="svc-evil@example.test",
                                  phone="09171234567")
        with self.assertRaises(RegistrationError):
            RegistrationService.submit(registration=reg, raw_password=PASSWORD)

    def test_duplicate_username_rejected(self):
        self._submit()
        resp = self._submit(username="TINA.TEST", email="other@example.test")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(RegistrationRequest.objects.filter(username__iexact="tina.test").count(), 1)

    def test_duplicate_email_rejected(self):
        self._submit()
        resp = self._submit(username="other.name", email="TINA@EXAMPLE.TEST")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(RegistrationRequest.objects.count(), 1)

    def test_invalid_phone_rejected(self):
        resp = self._submit(phone="1234")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(RegistrationRequest.objects.count(), 0)

    def test_text_fields_are_stored_in_uppercase(self):
        """Identity data is normalised once, on the way in.

        The registration, the linked account and (after approval) the donor
        record and the printed interview sheet all read the same values, so the
        case is fixed here rather than in each screen.
        """
        resp = self._submit(first_name="tina", middle_name="m", last_name="test",
                            email="Tina@Example.test", phone="0917 123 4567",
                            address="1 test st", municipality="zamboanga city",
                            province="zamboanga del sur",
                            interview_notes="mild penicillin allergy.")
        self.assertRedirects(resp, reverse("public_registration:register_done"))
        reg = RegistrationRequest.objects.get(username="tina.test")
        self.assertEqual(reg.first_name, "TINA")
        self.assertEqual(reg.middle_name, "M")
        self.assertEqual(reg.last_name, "TEST")
        self.assertEqual(reg.email, "TINA@EXAMPLE.TEST")
        self.assertEqual(reg.address, "1 TEST ST")
        self.assertEqual(reg.municipality, "ZAMBOANGA CITY")
        self.assertEqual(reg.province, "ZAMBOANGA DEL SUR")
        # The username is a login credential and keeps its case.
        self.assertEqual(reg.username, "tina.test")
        user = User.objects.get(username="tina.test")
        self.assertEqual(user.first_name, "TINA")
        self.assertEqual(user.email, "TINA@EXAMPLE.TEST")
        self.assertEqual(user.phone, "0917 123 4567")
        # Narrative notes stay readable as the applicant wrote them.
        from accounts.models import RegistrationInterview
        self.assertEqual(RegistrationInterview.objects.get(registration=reg).notes,
                         "mild penicillin allergy.")

    def test_requester_organization_is_stored_in_uppercase(self):
        resp = self._submit(role="REQUESTER", username="req.caps",
                            email="req.caps@example.test",
                            organization_name="test hospital")
        self.assertRedirects(resp, reverse("public_registration:register_done"))
        reg = RegistrationRequest.objects.get(username="req.caps")
        self.assertEqual(reg.organization_name, "TEST HOSPITAL")

    def test_requester_requires_organization(self):
        resp = self._submit(role="REQUESTER", username="req.one", email="req@example.test")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(RegistrationRequest.objects.exists())
        resp = self._submit(role="REQUESTER", username="req.one", email="req@example.test",
                            organization_name="Test Hospital")
        self.assertRedirects(resp, reverse("public_registration:register_done"))

    def test_pending_user_cannot_log_in_sees_pending_message(self):
        self._submit()
        resp = self.client.post(reverse("accounts:login"),
                                {"username": "tina.test", "password": PASSWORD})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "pending admin approval")

    def test_wrong_password_does_not_leak_pending_status(self):
        self._submit()
        resp = self.client.post(reverse("accounts:login"),
                                {"username": "tina.test", "password": "WrongPass999!"})
        self.assertNotContains(resp, "pending admin approval")


@override_settings(SMS_PROVIDER="mock", SMS_API_KEY="")
class DonorInterviewTests(TestCase):
    """The donor interview sheet is captured during DONOR self-registration,
    stored on RegistrationInterview for staff review, and never gates approval
    by itself (clinical eligibility is decided at screening, not here)."""

    def setUp(self):
        make_registration_templates()
        self.admin = make_user("iv-admin", role="ADMIN")

    def _submit(self, **overrides):
        return self.client.post(reverse("public_registration:register"),
                                donor_form_data(**overrides))

    def test_donor_interview_stored_with_registration(self):
        resp = self._submit(interview_notes="Mild penicillin allergy.")
        self.assertRedirects(resp, reverse("public_registration:register_done"))
        reg = RegistrationRequest.objects.get(username="tina.test")
        from accounts.models import RegistrationInterview
        interview = RegistrationInterview.objects.get(registration=reg)
        # "yes"/"no" radios are normalised to real booleans on save.
        self.assertTrue(interview.q01)
        self.assertFalse(interview.q23)
        self.assertTrue(interview.declaration)
        self.assertEqual(interview.notes, "Mild penicillin allergy.")

    def test_donor_requires_every_answer(self):
        data = donor_form_data()
        del data["q23"]  # leave one question unanswered
        resp = self.client.post(reverse("public_registration:register"), data)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(RegistrationRequest.objects.filter(username="tina.test").exists())

    def test_sex_is_required_for_donors(self):
        data = donor_form_data(sex="")
        resp = self.client.post(reverse("public_registration:register"), data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Sex is required for donors.")
        self.assertFalse(RegistrationRequest.objects.exists())

    def test_male_applicant_never_answers_the_female_block(self):
        data = donor_form_data(sex="MALE")
        for key in ("q33", "q35", "q36"):
            data.pop(key)
        resp = self.client.post(reverse("public_registration:register"), data)
        self.assertRedirects(resp, reverse("public_registration:register_done"))
        reg = RegistrationRequest.objects.get(username="tina.test")
        self.assertEqual(reg.sex, "MALE")
        self.assertIsNone(reg.interview.q33)

    def test_female_applicant_answers_the_female_block(self):
        data = donor_form_data(sex="FEMALE")
        data.pop("q33")
        resp = self.client.post(reverse("public_registration:register"), data)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(RegistrationRequest.objects.exists())

    def test_donor_requires_declaration(self):
        resp = self._submit(declaration="")  # certification unchecked
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(RegistrationRequest.objects.exists())

    def test_requester_registration_has_no_interview(self):
        from accounts.models import RegistrationInterview
        resp = self._submit(role="REQUESTER", username="iv-req",
                            email="iv-req@example.test", organization_name="IV Hospital")
        self.assertRedirects(resp, reverse("public_registration:register_done"))
        reg = RegistrationRequest.objects.get(username="iv-req")
        self.assertFalse(RegistrationInterview.objects.filter(registration=reg).exists())

    def test_review_page_shows_interview_answers(self):
        self._submit(interview_notes="Recent dengue in household.")
        reg = RegistrationRequest.objects.get(username="tina.test")
        self.client.login(username="iv-admin", password=PASSWORD)
        resp = self.client.get(reverse("accounts:registration_review", args=[reg.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Donor Interview Sheet")
        self.assertContains(resp, "1. Do you feel well and healthy today?")
        self.assertContains(resp, "Recent dengue in household.")


class RegistrationStepperTests(TestCase):
    """The public sign-up page presents the form as a stepper.

    The stepper is presentation only: it must keep the original POST
    contract (one form, one request, every field), so the server stays the
    only place where the rules live.
    """

    def setUp(self):
        make_registration_templates()

    def _get(self):
        resp = self.client.get(reverse("public_registration:register"))
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_page_renders_one_form_with_every_step(self):
        content = self._get()
        self.assertEqual(content.count("<form"), 1)
        self.assertEqual(content.count("data-step-panel"), 7)  # one panel per step
        self.assertIn("data-step-nav", content)
        self.assertIn("register-stepper.js", content)

    def test_role_is_a_real_radio_choice(self):
        # The role cards are radio inputs, so the POST contract is unchanged
        # and a crafted role still has to pass the form's choice validation.
        content = self._get()
        self.assertIn('name="role"', content)
        self.assertIn('value="DONOR"', content)
        self.assertIn('value="REQUESTER"', content)

    def test_review_step_mirrors_the_posted_values(self):
        data = donor_form_data()
        del data["declaration"]  # force a re-render with errors
        resp = self.client.post(reverse("public_registration:register"), data)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn('<dd data-review-field="username">tina.test</dd>', content)
        self.assertIn('<dd data-review-field="role">Donor</dd>', content)
        self.assertIn('<dd data-review-field="email">tina@example.test</dd>', content)

    def test_role_specific_steps_are_marked_for_the_stepper(self):
        # The stepper needs to know which steps belong to which role, and
        # which fields are only required for that role.
        content = self._get()
        self.assertIn('data-step-key="donor" data-step-for="DONOR"', content)
        self.assertIn('data-step-key="requester" data-step-for="REQUESTER"', content)
        self.assertIn('data-required-for="DONOR"', content)
        self.assertIn('data-required-for="REQUESTER"', content)



@override_settings(SMS_PROVIDER="mock", SMS_API_KEY="")
class RegisterSheetPreviewTests(TestCase):
    """The pre-submission preview: the real interview sheet, built from an
    unsaved application and never written to the database."""

    def setUp(self):
        make_registration_templates()

    def _preview(self, **overrides):
        return self.client.post(reverse("public_registration:register_preview"),
                                donor_form_data(**overrides))

    def test_donor_preview_renders_the_interview_sheet(self):
        resp = self._preview()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["X-Register-Preview"], "sheet")
        content = resp.content.decode()
        self.assertIn("Blood Donor Interview Sheet", content)
        self.assertIn("Preview - not yet a donor record", content)
        self.assertIn("PREVIEW - built from your registration form", content)
        # The official questionnaire staff review is on the same document.
        self.assertIn("feel well and healthy today", content)
        # The form asks the sheet's own items, so every answer lands on its
        # item; there is no second sign-up listing to print.
        self.assertIn("In the past 4 WEEKS have you taken any medications", content)
        self.assertNotIn("Self-declaration recorded at sign-up", content)
        # A preview is not a record: no PDF download, and never the password.
        self.assertNotIn("Download PDF", content)
        self.assertNotIn(PASSWORD, content)

    def test_preview_upper_cases_the_details_like_storage_will(self):
        content = self._preview(first_name="Tina", address="1 Test St").content.decode()
        self.assertIn("TINA M TEST", content)
        self.assertIn("1 TEST ST, ZAMBOANGA CITY, ZAMBOANGA DEL SUR", content)
        self.assertNotIn("Tina M Test", content)

    def test_prefilled_declaration_is_ticked(self):
        content = self._preview().content.decode()
        # The form asks all 35 YES/NO items of the sheet and the applicant
        # answered every one, so every box carries a tick - YES or NO.
        self.assertEqual(content.count('<span class="cbox">X</span>'), 35)

    def test_preview_writes_nothing(self):
        from accounts.models import RegistrationInterview
        from audit.models import AuditLog
        from donors.models import Donor, DonorInterviewSheet, InterviewAnswer
        self._preview()
        for model in (User, RegistrationRequest, RegistrationInterview, Donor,
                      DonorInterviewSheet, InterviewAnswer, AuditLog):
            self.assertEqual(model.objects.count(), 0, model.__name__)

    def test_requester_gets_an_explanation_not_a_sheet(self):
        resp = self._preview(role="REQUESTER", username="prev-req",
                             email="prev-req@example.test",
                             organization_name="Preview Hospital")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["X-Register-Preview"], "unavailable")
        self.assertContains(resp, "donor registrations only")
        self.assertNotContains(resp, "Blood Donor Interview Sheet")

    def test_invalid_application_lists_what_to_fix(self):
        resp = self._preview(declaration="")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["X-Register-Preview"], "unavailable")
        self.assertContains(resp, "You must certify the interview before submitting.")
        self.assertContains(resp, "Nothing has been submitted")
        self.assertFalse(User.objects.exists())

    def test_authenticated_user_is_sent_to_the_dashboard(self):
        make_user("prev-user", role="DONOR")
        self.client.login(username="prev-user", password=PASSWORD)
        resp = self._preview()
        self.assertRedirects(resp, reverse("core:dashboard"))

    def test_get_is_not_allowed(self):
        resp = self.client.get(reverse("public_registration:register_preview"))
        self.assertEqual(resp.status_code, 405)

    def test_register_page_offers_the_preview(self):
        content = self.client.get(reverse("public_registration:register")).content.decode()
        self.assertIn("data-sheet-preview-open", content)
        self.assertIn('formaction="/register/preview/"', content)
        self.assertIn('formtarget="_blank"', content)
        self.assertIn('data-preview-for="DONOR"', content)


@override_settings(SMS_PROVIDER="mock", SMS_API_KEY="")
class RegistrationReviewTests(TestCase):
    def setUp(self):
        make_registration_templates()
        self.admin = make_user("rev-admin", role="ADMIN")
        self.staff = make_user("rev-staff", role="STAFF")
        applicant = make_user("rev-donor", role="DONOR", is_active=False)
        self.reg = RegistrationRequest.objects.create(
            first_name="Pat", last_name="Applicant", username=applicant.username,
            email="pat@example.test", phone="09171234567", role="DONOR", user=applicant)

    def _post(self, action, reason="", **extra):
        return self.client.post(reverse("accounts:registration_review", args=[self.reg.pk]),
                                {"action": action, "reason": reason, **extra})

    def test_approve_activates_and_notifies(self):
        self.client.login(username="rev-admin", password=PASSWORD)
        self._post("approve")
        self.reg.refresh_from_db()
        self.assertEqual(self.reg.status, "APPROVED")
        self.assertEqual(self.reg.reviewed_by, self.admin)
        self.assertIsNotNone(self.reg.reviewed_at)
        self.assertTrue(self.reg.user.is_active)
        self.assertTrue(self.reg.user.notifications.filter(
            template__code="registration_approved").exists())
        from audit.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(action="REGISTRATION_APPROVED").exists())

    def test_approve_lets_user_log_in(self):
        self.client.login(username="rev-admin", password=PASSWORD)
        self._post("approve")
        self.client.logout()
        ok = self.client.login(username="rev-donor", password=PASSWORD)
        self.assertTrue(ok)

    def test_reject_requires_reason_and_token(self):
        self.client.login(username="rev-admin", password=PASSWORD)
        self._post("reject")
        self.reg.refresh_from_db()
        self.assertEqual(self.reg.status, "PENDING")
        self._post("reject", reason="  ")
        self.reg.refresh_from_db()
        self.assertEqual(self.reg.status, "PENDING")
        self._post("reject", reason="Docs incomplete", confirm="WRONG")
        self.reg.refresh_from_db()
        self.assertEqual(self.reg.status, "PENDING")
        self._post("reject", reason="Docs incomplete", confirm="REJECT")
        self.reg.refresh_from_db()
        self.assertEqual(self.reg.status, "REJECTED")
        self.assertEqual(self.reg.rejection_reason, "Docs incomplete")
        self.assertFalse(self.reg.user.is_active)
        self.assertTrue(self.reg.user.notifications.filter(
            template__code="registration_rejected").exists())
        from audit.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(action="REGISTRATION_REJECTED").exists())

    def test_rejected_user_sees_reason_on_login(self):
        from accounts.services import RegistrationService
        RegistrationService.review(self.reg, decision=RegistrationRequest.Status.REJECTED,
                                   actor=self.admin, reason="ID photo unreadable.")
        resp = self.client.post(reverse("accounts:login"),
                                {"username": "rev-donor", "password": PASSWORD})
        self.assertContains(resp, "ID photo unreadable")

    def test_review_is_one_shot(self):
        from accounts.services import RegistrationError, RegistrationService
        RegistrationService.review(self.reg, decision=RegistrationRequest.Status.APPROVED,
                                   actor=self.admin)
        with self.assertRaises(RegistrationError):
            RegistrationService.review(self.reg, decision=RegistrationRequest.Status.REJECTED,
                                       actor=self.admin, reason="too late")

    def test_self_approval_guard(self):
        from accounts.services import RegistrationError, RegistrationService
        me = make_user("rev-self", role="ADMIN", is_active=False)
        own = RegistrationRequest.objects.create(
            first_name="Sel", last_name="F", username=me.username, email="sel@example.test",
            phone="09171234567", role="DONOR", user=me)
        me.refresh_from_db()
        with self.assertRaises(RegistrationError):
            RegistrationService.review(own, decision=RegistrationRequest.Status.APPROVED, actor=me)

    def test_modal_fragment_served_on_htmx_get(self):
        self.client.login(username="rev-admin", password=PASSWORD)
        resp = self.client.get(reverse("accounts:registration_review", args=[self.reg.pk]),
                               HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "<html")


@override_settings(SMS_PROVIDER="mock", SMS_API_KEY="")
class RequesterApprovalLinkTests(TestCase):
    """Approving a REQUESTER must attach the organization profile.

    Without it the account can sign in but is stuck on the dashboard's
    "No organization linked" banner and blocked from creating requests."""

    def setUp(self):
        make_registration_templates()
        self.admin = make_user("link-admin", role="ADMIN")
        applicant = make_user("link-req", role="REQUESTER", is_active=False)
        self.reg = RegistrationRequest.objects.create(
            first_name="Req", last_name="Tester", username=applicant.username,
            email="link@example.test", phone="09171234567", role="REQUESTER",
            organization_name="Link Test Hospital", user=applicant)

    def _approve(self):
        from accounts.services import RegistrationService
        RegistrationService.review(self.reg, decision=RegistrationRequest.Status.APPROVED,
                                   actor=self.admin)

    def test_approval_creates_organization_and_profile(self):
        from requests.models import Organization, RequesterProfile
        self._approve()
        organization = Organization.objects.get(name="Link Test Hospital")
        self.assertEqual(RequesterProfile.objects.get(user__username="link-req").organization,
                         organization)
        self.client.login(username="link-req", password=PASSWORD)
        resp = self.client.get(reverse("core:dashboard"))
        self.assertNotContains(resp, "No organization linked")
        self.assertContains(resp, "Requests for Link Test Hospital.")

    def test_approval_reuses_existing_organization_ignoring_case(self):
        from requests.models import Organization, RequesterProfile
        existing = Organization.objects.create(name="link test hospital", org_type="CLINIC")
        self._approve()
        self.assertEqual(Organization.objects.filter(name__iexact="link test hospital").count(), 1)
        self.assertEqual(RequesterProfile.objects.get(user__username="link-req").organization,
                         existing)

    def test_blank_organization_name_leaves_account_unlinked(self):
        from requests.models import RequesterProfile
        self.reg.organization_name = "   "
        self.reg.save(update_fields=["organization_name"])
        self._approve()
        self.reg.user.refresh_from_db()
        self.assertTrue(self.reg.user.is_active)
        self.assertFalse(RequesterProfile.objects.filter(user__username="link-req").exists())

    def test_pre_existing_profile_is_not_repointed(self):
        from requests.models import Organization, RequesterProfile
        org = Organization.objects.create(name="Already On File", org_type="CLINIC")
        RequesterProfile.objects.create(user=self.reg.user, organization=org)
        self._approve()
        self.assertEqual(RequesterProfile.objects.get(user__username="link-req").organization, org)

    def test_donor_approval_does_not_create_requester_profile(self):
        from requests.models import RequesterProfile
        applicant = make_user("link-donor", role="DONOR", is_active=False)
        reg = RegistrationRequest.objects.create(
            first_name="Don", last_name="Or", username="link-donor",
            email="link-donor@example.test", phone="09171234567", role="DONOR", user=applicant)
        from accounts.services import RegistrationService
        RegistrationService.review(reg, decision=RegistrationRequest.Status.APPROVED,
                                   actor=self.admin)
        self.assertFalse(RequesterProfile.objects.filter(user__username="link-donor").exists())


@override_settings(SMS_PROVIDER="mock", SMS_API_KEY="")
class DonorApprovalLinkTests(TestCase):
    """Approving a DONOR must create the linked Donor record.

    Without it the account activates but the donor dashboard falls through to
    the "No donor record linked to your account" screen, so the new donor can
    never see their profile/history."""

    def setUp(self):
        make_registration_templates()
        self.admin = make_user("dlink-admin", role="ADMIN")

    def _donor_reg(self, **overrides):
        username = overrides.pop("username", "dlink-donor")
        applicant = make_user(username, role="DONOR", is_active=False)
        defaults = dict(first_name="Dona", last_name="Link", username=username,
                        email="dlink@example.test", phone="09171234567", role="DONOR",
                        date_of_birth=date(1995, 5, 5), address="1 Donor St",
                        municipality="Zamboanga City", province="Zamboanga del Sur",
                        user=applicant)
        defaults.update(overrides)
        return RegistrationRequest.objects.create(**defaults)

    def _approve(self, reg):
        from accounts.services import RegistrationService
        RegistrationService.review(reg, decision=RegistrationRequest.Status.APPROVED,
                                   actor=self.admin)

    def test_approval_creates_linked_donor(self):
        from donors.models import Donor
        reg = self._donor_reg()
        self._approve(reg)
        donor = Donor.objects.get(user=reg.user)
        # The registration predates the upper-case rule (it was inserted
        # directly), and approval still produces the printable form's case.
        self.assertEqual(donor.first_name, "DONA")
        self.assertEqual(donor.last_name, "LINK")
        self.assertEqual(str(donor.date_of_birth), "1995-05-05")
        self.assertEqual(donor.contact_number, "09171234567")
        self.assertEqual(donor.municipality, "ZAMBOANGA CITY")
        # sex is not collected at signup → neutral default, staff edits later
        self.assertEqual(donor.sex, Donor.Sex.UNDISCLOSED)

    def test_approval_generates_the_interview_sheet_prefilled(self):
        """The approved registration must leave a ready-to-print interview sheet."""
        from accounts.models import RegistrationInterview
        from donors.interview import ALL_ITEMS
        from donors.models import Donor, DonorInterviewSheet

        reg = self._donor_reg(username="dlink-sheet")
        RegistrationInterview.objects.create(registration=reg, q01=True, q07=True)
        self._approve(reg)
        sheet = DonorInterviewSheet.objects.get(donor=Donor.objects.get(user=reg.user))
        self.assertEqual(sheet.registration_id, reg.pk)
        self.assertEqual(sheet.address_street,
                         "1 DONOR ST, ZAMBOANGA CITY, ZAMBOANGA DEL SUR")
        self.assertEqual(sheet.answers.count(), len(ALL_ITEMS))
        self.assertEqual(sheet.answer_for("Q01"), "YES")
        self.assertEqual(sheet.answer_for("Q07"), "YES")
        self.assertEqual(sheet.answer_for("Q16"), "")      # blank is not "No"

    def test_approved_donor_dashboard_no_longer_warns(self):
        reg = self._donor_reg(username="dlink-dash")
        self._approve(reg)
        self.client.login(username="dlink-dash", password=PASSWORD)
        resp = self.client.get(reverse("core:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "No donor record linked")

    def test_existing_donor_profile_is_not_duplicated(self):
        from donors.models import Donor
        reg = self._donor_reg(username="dlink-existing")
        Donor.objects.create(user=reg.user, first_name="Pre", last_name="Existing",
                             date_of_birth=date(1990, 1, 1), sex="MALE",
                             contact_number="09000000000")
        self._approve(reg)
        self.assertEqual(Donor.objects.filter(user=reg.user).count(), 1)
        self.assertEqual(Donor.objects.get(user=reg.user).first_name, "Pre")

    def test_missing_dob_leaves_account_active_without_donor(self):
        from donors.models import Donor
        reg = self._donor_reg(username="dlink-nodobj", date_of_birth=None)
        self._approve(reg)
        reg.user.refresh_from_db()
        self.assertTrue(reg.user.is_active)
        self.assertFalse(Donor.objects.filter(user=reg.user).exists())


class BackfillDonorProfilesCommandTests(TestCase):
    """backfill_donor_profiles repairs approved donors left unlinked before the
    approval flow created Donor records. Insert-only + idempotent."""

    def setUp(self):
        make_registration_templates()

    def _approved_donor_without_profile(self, username, **overrides):
        user = make_user(username, role="DONOR")  # already active (as post-approval)
        defaults = dict(first_name="Old", last_name="Approved", username=username,
                        email=f"{username}@example.test", phone="09171234567",
                        role="DONOR", date_of_birth=date(1988, 3, 3), user=user,
                        status=RegistrationRequest.Status.APPROVED)
        defaults.update(overrides)
        return RegistrationRequest.objects.create(**defaults)

    def test_creates_missing_donor_and_is_idempotent(self):
        from donors.models import Donor
        reg = self._approved_donor_without_profile("bf-donor")
        call_command("backfill_donor_profiles", stdout=StringIO())
        self.assertTrue(Donor.objects.filter(user=reg.user).exists())
        # running again must not duplicate
        call_command("backfill_donor_profiles", stdout=StringIO())
        self.assertEqual(Donor.objects.filter(user=reg.user).count(), 1)
        from audit.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(action="DONOR_PROFILE_BACKFILLED").exists())

    def test_dry_run_writes_nothing(self):
        from donors.models import Donor
        reg = self._approved_donor_without_profile("bf-dry")
        call_command("backfill_donor_profiles", "--dry-run", stdout=StringIO())
        self.assertFalse(Donor.objects.filter(user=reg.user).exists())

    def test_skips_missing_dob(self):
        from donors.models import Donor
        reg = self._approved_donor_without_profile("bf-nodobj", date_of_birth=None)
        call_command("backfill_donor_profiles", stdout=StringIO())
        self.assertFalse(Donor.objects.filter(user=reg.user).exists())
        self.assertTrue(reg.user.is_active)


@override_settings(SMS_PROVIDER="mock", SMS_API_KEY="")
class RegistrationBadgeTests(TestCase):
    """Sidebar pill: pending registrations, admin only."""

    def setUp(self):
        make_registration_templates()
        self.admin = make_user("badge-admin", role="ADMIN")
        self.staff = make_user("badge-staff", role="STAFF")

    def _pending(self, username):
        user = make_user(username, role="DONOR", is_active=False)
        return RegistrationRequest.objects.create(
            first_name="P", last_name="End", username=username,
            email=f"{username}@example.test", phone="09171234567", role="DONOR", user=user)

    def test_counts_only_pending(self):
        from accounts.services import RegistrationService
        self._pending("badge-p2")
        self._pending("badge-p3")
        self.assertEqual(RegistrationService.pending_count(), 2)
        reviewed = self._pending("badge-p4")
        RegistrationService.review(reviewed, decision=RegistrationRequest.Status.REJECTED,
                                   actor=self.admin, reason="nope")
        self.assertEqual(RegistrationService.pending_count(), 2)

    def test_endpoint_renders_pill_for_admin(self):
        self._pending("badge-p5")
        self.client.login(username="badge-admin", password=PASSWORD)
        resp = self.client.get(reverse("accounts:registration_badge"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, ">1<")

    def test_endpoint_denies_non_admin(self):
        self.client.login(username="badge-staff", password=PASSWORD)
        resp = self.client.get(reverse("accounts:registration_badge"))
        self.assertEqual(resp.status_code, 403)


@override_settings(SMS_PROVIDER="mock", SMS_API_KEY="")
class RegistrationAccessTests(TestCase):
    def setUp(self):
        self.donor = make_user("acc-donor2", role="DONOR")
        self.requester = make_user("acc-req2", role="REQUESTER")

    def _assert_denied(self, name, url_name):
        self.client.login(username=name, password=PASSWORD)
        resp = self.client.get(reverse(url_name))
        self.assertIn(resp.status_code, (403, 302), f"{name} -> {url_name}")

    def test_anonymous_redirected_from_admin_pages(self):
        for url_name in ("accounts:registration_list", "accounts:registration_review"):
            url = reverse(url_name, args=[1]) if "review" in url_name else reverse(url_name)
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, url_name)
            self.assertIn("login", resp.url)

    def test_non_admins_denied(self):
        for name in ("acc-donor2", "acc-req2", None):
            if name is None:
                name = "acc-staff2"
                make_user(name, role="STAFF")
            self._assert_denied(name, "accounts:registration_list")
            self.client.logout()

    def test_post_review_denied_for_staff(self):
        make_user("acc-staff3", role="STAFF")
        self.client.login(username="acc-staff3", password=PASSWORD)
        resp = self.client.post(reverse("accounts:registration_review", args=[1]),
                                {"action": "approve"})
        self.assertIn(resp.status_code, (403, 302))


@override_settings(SMS_PROVIDER="mock", SMS_API_KEY="")
class RegistrationSheetPreviewTests(TestCase):
    """A PENDING donor registration can be previewed as the sheet it will
    create, so staff verify the pre-fill before approval. Nothing is written
    and the approval decision stays admin-only."""

    def setUp(self):
        make_registration_templates()
        self.admin = make_user("prev-admin", role="ADMIN")
        self.staff = make_user("prev-staff", role="STAFF")
        self.donor = make_user("prev-donor", role="DONOR")
        self.requester = make_user("prev-requester", role="REQUESTER")
        applicant = make_user("prev-applicant", role="DONOR", is_active=False)
        # Deliberately mixed case: the preview normalises it, so a registration
        # that predates the upper-case rule still previews correctly.
        self.reg = RegistrationRequest.objects.create(
            first_name="nena", middle_name="s", last_name="nakasalin",
            username="prev-applicant", email="nena@example.test", phone="09171234567",
            role="DONOR", date_of_birth=date(1998, 4, 4),
            address="demo street", municipality="zamboanga city",
            province="zamboanga del sur", user=applicant)
        from accounts.models import RegistrationInterview
        RegistrationInterview.objects.create(
            registration=self.reg, q01=True, q07=True, declaration=True)

    def _url(self, name="accounts:registration_sheet_preview", pk=None):
        return reverse(name, args=[pk or self.reg.pk])

    def test_staff_preview_shows_the_uppercase_prefill(self):
        self.client.login(username="prev-staff", password=PASSWORD)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn("NENA S NAKASALIN", content)
        self.assertIn("DEMO STREET, ZAMBOANGA CITY, ZAMBOANGA DEL SUR", content)
        self.assertIn("PREVIEW - built from a pending registration", content)
        # Staff cannot reach the admin-only review screen, so the Close button
        # must not lead to a 403.
        self.assertIn(reverse("core:dashboard"), content)

    def test_preview_answers_follow_the_self_declaration(self):
        from donors.services import InterviewSheetService
        sheet, answers = InterviewSheetService.preview_for_registration(self.reg)
        self.assertEqual(answers["Q01"].answer, "YES")
        self.assertEqual(answers["Q07"].answer, "YES")
        self.assertNotIn("Q33", answers)                 # female block not asked
        self.assertEqual(sheet.address_street,
                         "DEMO STREET, ZAMBOANGA CITY, ZAMBOANGA DEL SUR")
        self.assertEqual(sheet.donor.donor_code, f"PREVIEW-{self.reg.pk:05d}")
        self.assertEqual(sheet.donor.sex, "UNDISCLOSED")

    def test_preview_and_pdf_write_nothing(self):
        from audit.models import AuditLog
        from donors.models import Donor, DonorInterviewSheet

        self.client.login(username="prev-admin", password=PASSWORD)
        self.assertEqual(self.client.get(self._url()).status_code, 200)
        pdf = self.client.get(self._url("accounts:registration_sheet_pdf"))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF-"))
        self.assertFalse(Donor.objects.exists())
        self.assertFalse(DonorInterviewSheet.objects.exists())
        self.reg.refresh_from_db()
        self.assertEqual(self.reg.status, "PENDING")
        self.assertFalse(AuditLog.objects.filter(action="INTERVIEW_SHEET_CREATED").exists())

    def test_review_page_links_to_the_preview(self):
        self.client.login(username="prev-admin", password=PASSWORD)
        resp = self.client.get(reverse("accounts:registration_review", args=[self.reg.pk]))
        self.assertContains(resp, self._url())

    def test_staff_can_preview_but_still_cannot_review(self):
        self.client.login(username="prev-staff", password=PASSWORD)
        self.assertEqual(self.client.get(self._url()).status_code, 200)
        resp = self.client.get(reverse("accounts:registration_review", args=[self.reg.pk]))
        self.assertEqual(resp.status_code, 403)

    def test_donor_and_requester_are_denied(self):
        for username in ("prev-donor", "prev-requester"):
            self.client.login(username=username, password=PASSWORD)
            for name in ("accounts:registration_sheet_preview",
                         "accounts:registration_sheet_pdf"):
                resp = self.client.get(self._url(name))
                self.assertIn(resp.status_code, (302, 403), f"{username} -> {name}")
            self.client.logout()

    def test_requester_registration_has_no_preview(self):
        applicant = make_user("prev-req-applicant", role="REQUESTER", is_active=False)
        reg = RegistrationRequest.objects.create(
            first_name="req", last_name="tester", username="prev-req-applicant",
            email="prev-req@example.test", phone="09171234567", role="REQUESTER",
            organization_name="prev hospital", user=applicant)
        self.client.login(username="prev-admin", password=PASSWORD)
        self.assertEqual(self.client.get(self._url(pk=reg.pk)).status_code, 404)

    def test_approved_registration_is_no_longer_a_preview(self):
        from accounts.services import RegistrationService
        RegistrationService.review(self.reg, decision=RegistrationRequest.Status.APPROVED,
                                   actor=self.admin)
        self.client.login(username="prev-admin", password=PASSWORD)
        # After approval the real sheet exists; the preview endpoint stops
        # answering so it can never shadow the donor's record.
        self.assertEqual(self.client.get(self._url()).status_code, 404)

