"""Audit trail: append-only guarantees and event capture."""
from django.test import TestCase

from audit.models import AuditLog
from audit import services as audit
from core.testing import make_user


class AuditImmutabilityTests(TestCase):
    def setUp(self):
        self.admin = make_user("aud-admin", role="ADMIN")

    def test_log_creates_entry(self):
        audit.log(None, user=self.admin, action="TEST_EVENT", module="tests",
                  description="hello")
        entry = AuditLog.objects.get(action="TEST_EVENT")
        self.assertEqual(entry.user, self.admin)
        self.assertEqual(entry.module, "tests")

    def test_existing_row_cannot_be_updated(self):
        audit.log(None, user=self.admin, action="TEST_EVENT2", module="tests")
        entry = AuditLog.objects.get(action="TEST_EVENT2")
        entry.description = "tampered"
        with self.assertRaises(RuntimeError):
            entry.save()

    def test_instance_delete_blocked(self):
        audit.log(None, user=self.admin, action="TEST_EVENT3", module="tests")
        entry = AuditLog.objects.get(action="TEST_EVENT3")
        with self.assertRaises(RuntimeError):
            entry.delete()
        self.assertTrue(AuditLog.objects.filter(action="TEST_EVENT3").exists())

    def test_queryset_bulk_delete_blocked(self):
        audit.log(None, user=self.admin, action="TEST_EVENT4", module="tests")
        with self.assertRaises(RuntimeError):
            AuditLog.objects.filter(action="TEST_EVENT4").delete()
        self.assertTrue(AuditLog.objects.filter(action="TEST_EVENT4").exists())

    def test_snapshot_captured_for_object(self):
        donor = make_user("aud-donor", role="DONOR")
        audit.log(None, user=self.admin, action="OBJ_EVENT", module="tests", obj=donor)
        entry = AuditLog.objects.get(action="OBJ_EVENT")
        self.assertEqual(entry.object_type, "User")
        self.assertEqual(entry.after_state["username"], "aud-donor")
