"""Reports: role gating, rendering and CSV export."""
from django.test import TestCase
from django.urls import reverse

from core.testing import make_blood_type, make_component, make_bag, make_donor, make_user


class ReportAccessTests(TestCase):
    def setUp(self):
        self.staff = make_user("rp-staff", role="STAFF")
        self.admin = make_user("rp-admin", role="ADMIN")
        self.donor_user = make_user("rp-donor", role="DONOR")
        make_donor(user=self.donor_user)

    def test_staff_can_run_shared_report(self):
        self.client.login(username="rp-staff", password="Test12345!")
        resp = self.client.get(reverse("reports:run", kwargs={"key": "inventory"}))
        self.assertEqual(resp.status_code, 200)

    def test_audit_report_is_admin_only(self):
        self.client.login(username="rp-staff", password="Test12345!")
        resp = self.client.get(reverse("reports:run", kwargs={"key": "audit"}))
        self.assertEqual(resp.status_code, 403)
        self.client.login(username="rp-admin", password="Test12345!")
        resp = self.client.get(reverse("reports:run", kwargs={"key": "audit"}))
        self.assertEqual(resp.status_code, 200)

    def test_donor_blocked_from_reports(self):
        self.client.login(username="rp-donor", password="Test12345!")
        resp = self.client.get(reverse("reports:run", kwargs={"key": "inventory"}))
        self.assertIn(resp.status_code, (403, 302))

    def test_unknown_report_key_denied(self):
        self.client.login(username="rp-admin", password="Test12345!")
        resp = self.client.get(reverse("reports:run", kwargs={"key": "nope"}))
        self.assertIn(resp.status_code, (403, 404))


class ReportDataTests(TestCase):
    def setUp(self):
        self.admin = make_user("rp-admin2", role="ADMIN")
        bt = make_blood_type("O", "NEG")
        comp = make_component("wb", "Whole Blood")
        donor = make_donor(blood_type=bt)
        for _ in range(3):
            make_bag(donor, blood_type=bt, component=comp, status="AVAILABLE")
        self.client.login(username="rp-admin2", password="Test12345!")

    def test_csv_export_contains_rows(self):
        resp = self.client.get(reverse("reports:export", kwargs={"key": "inventory"}))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])
        text = resp.content.decode()
        self.assertIn("generated", text.splitlines()[0])
        self.assertGreaterEqual(len(text.strip().splitlines()), 4)  # header banner + columns + rows

    def test_inventory_report_counts_bags(self):
        resp = self.client.get(reverse("reports:run", kwargs={"key": "inventory"}))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Whole Blood", resp.content.decode())
