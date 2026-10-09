"""Accounts: role model, login gating, user management permissions."""
from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import RegistrationRequest, User
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
    """A complete, valid donor interview sheet (YES/NO radios + declaration)."""
    data = {
        "felt_well_today": "yes", "on_medication": "no", "recent_illness": "no",
        "prior_transfusion": "no", "reactive_test": "no", "tattoo_piercing": "no",
        "dental_procedure": "no", "recent_vaccination": "no", "high_risk_behavior": "no",
        "pregnant_or_breastfeeding": "no", "previously_deferred": "no",
        "declaration": "on", "interview_notes": "",
    }
    data.update(overrides)
    return data


def donor_form_data(**overrides):
    data = {"role": "DONOR", "first_name": "Tina", "middle_name": "M", "last_name": "Test",
            "username": "tina.test", "email": "tina@example.test", "phone": "09171234567",
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
        self.assertTrue(interview.felt_well_today)
        self.assertFalse(interview.reactive_test)
        self.assertTrue(interview.declaration)
        self.assertEqual(interview.notes, "Mild penicillin allergy.")

    def test_donor_requires_every_answer(self):
        data = donor_form_data()
        del data["reactive_test"]  # leave one question unanswered
        resp = self.client.post(reverse("public_registration:register"), data)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(RegistrationRequest.objects.filter(username="tina.test").exists())

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
        self.assertContains(resp, "I feel well and healthy today.")
        self.assertContains(resp, "Recent dengue in household.")



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
        self.assertEqual(donor.first_name, "Dona")
        self.assertEqual(donor.last_name, "Link")
        self.assertEqual(str(donor.date_of_birth), "1995-05-05")
        self.assertEqual(donor.contact_number, "09171234567")
        self.assertEqual(donor.municipality, "Zamboanga City")
        # sex is not collected at signup → neutral default, staff edits later
        self.assertEqual(donor.sex, Donor.Sex.UNDISCLOSED)

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
