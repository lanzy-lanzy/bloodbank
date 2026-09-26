"""Settings: typed values, admin-only mutation, audited changes."""
from django.test import TestCase
from django.urls import reverse

from core.testing import make_user
from settings_app.models import SystemSetting
from settings_app.services import get_bool_setting, get_int_setting, get_setting, set_setting


class TypedSettingTests(TestCase):
    def test_int_setting_roundtrip(self):
        set_setting("min_donation_interval_days", 60, value_type="int")
        self.assertEqual(get_setting("min_donation_interval_days"), 60)
        self.assertEqual(get_int_setting("min_donation_interval_days"), 60)

    def test_bool_setting(self):
        set_setting("some_flag", "on", value_type="bool")
        self.assertTrue(get_bool_setting("some_flag"))

    def test_missing_key_returns_default(self):
        self.assertIsNone(get_setting("does_not_exist"))
        self.assertEqual(get_int_setting("does_not_exist", 5), 5)

    def test_audit_written_on_change_with_actor(self):
        from audit.models import AuditLog
        admin = make_user("set-admin", role="ADMIN")
        set_setting("low_stock_threshold", 12, user=admin)
        self.assertTrue(AuditLog.objects.filter(
            action="SYSTEM_SETTING_CHANGED", description__contains="low_stock_threshold").exists())


class SettingsAccessTests(TestCase):
    def setUp(self):
        self.setting = SystemSetting.objects.create(key="low_stock_threshold", value="10",
                                                    value_type="int", category="inventory")
        self.admin = make_user("set-admin2", role="ADMIN")
        self.staff = make_user("set-staff", role="STAFF")

    def test_admin_can_quick_set(self):
        self.client.login(username="set-admin2", password="Test12345!")
        self.client.post(reverse("settings_app:quick_set", kwargs={"pk": self.setting.pk}),
                         {"value": "7"})
        self.setting.refresh_from_db()
        self.assertEqual(self.setting.value, "7")

    def test_staff_cannot_quick_set(self):
        self.client.login(username="set-staff", password="Test12345!")
        resp = self.client.post(reverse("settings_app:quick_set", kwargs={"pk": self.setting.pk}),
                                {"value": "99"})
        self.assertIn(resp.status_code, (403, 302))
        self.setting.refresh_from_db()
        self.assertEqual(self.setting.value, "10")

    def test_staff_blocked_from_settings_pages(self):
        self.client.login(username="set-staff", password="Test12345!")
        for name in ("settings_app:index", "settings_app:blood_bank"):
            resp = self.client.get(reverse(name))
            self.assertIn(resp.status_code, (403, 302), name)

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(reverse("settings_app:index"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)
