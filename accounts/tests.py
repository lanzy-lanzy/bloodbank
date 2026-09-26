"""Accounts: role model, login gating, user management permissions."""
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from core.testing import make_user


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
