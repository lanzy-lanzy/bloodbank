"""Inventory: state machine, release guard, expiry, compatibility engine."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from core.testing import (make_bag, make_blood_type, make_component, make_compat_rule,
                          make_donor, make_test_type, make_user)
from inventory.models import BloodBag, InventoryTransaction, TestResult, TestType
from inventory.services import CompatibilityService, InventoryError, InventoryService


def complete_tests_for(bag, staff):
    """Record + verify a NON_REACTIVE latest result for every active required test."""
    for tt in TestType.objects.filter(is_required=True, is_active=True):
        TestResult.objects.create(bag=bag, test_type=tt, result="e2e",
                                  result_status=TestResult.ResultStatus.NON_REACTIVE,
                                  performed_by=staff)
        tr = TestResult.objects.filter(bag=bag, test_type=tt).order_by("-performed_at", "-id").first()
        tr.verified_by = staff
        tr.verified_at = timezone.now()
        tr.save(update_fields=["verified_by", "verified_at"])


class StateMachineTests(TestCase):
    def setUp(self):
        self.staff = make_user("inv-staff", role="STAFF")
        self.donor = make_donor()
        self.comp = make_component("prbc", "PRBC", shelf_life=35)

    def test_illegal_transition_refused(self):
        bag = make_bag(self.donor, component=self.comp)
        with self.assertRaises(InventoryError):
            InventoryService.transition(bag.pk, "AVAILABLE", actor=self.staff)

    def test_terminal_states_have_no_moves(self):
        bag = make_bag(self.donor, component=self.comp, status="DISCARDED")
        for target in ("AVAILABLE", "TESTING", "QUARANTINED"):
            self.assertFalse(bag.can_transition_to(target))

    def test_transition_writes_ledger_and_audit(self):
        from audit.models import AuditLog
        make_test_type("Required X")
        bag = make_bag(self.donor, component=self.comp, status="TESTING")
        complete_tests_for(bag, self.staff)
        InventoryService.transition(bag.pk, "AVAILABLE", actor=self.staff, reason="cleared")
        txn = InventoryTransaction.objects.filter(bag=bag).order_by("-id").first()
        self.assertEqual((txn.previous_status, txn.new_status), ("TESTING", "AVAILABLE"))
        self.assertTrue(AuditLog.objects.filter(action="BLOOD_BAG_RELEASED").exists())


class ReleaseGuardTests(TestCase):
    def setUp(self):
        self.staff = make_user("inv-staff2", role="STAFF")
        self.donor = make_donor()
        self.comp = make_component("wb2", "WB2", shelf_life=21)

    def test_no_required_tests_configured_blocks_release(self):
        TestType.objects.all().delete()
        bag = make_bag(self.donor, component=self.comp, status="TESTING")
        with self.assertRaises(InventoryError):
            InventoryService.transition(bag.pk, "AVAILABLE", actor=self.staff)

    def test_missing_required_test_blocks_release(self):
        make_test_type("HIV")
        bag = make_bag(self.donor, component=self.comp, status="TESTING")
        with self.assertRaises(InventoryError):
            InventoryService.transition(bag.pk, "AVAILABLE", actor=self.staff)

    def test_unverified_result_blocks_release(self):
        make_test_type("HIV")
        bag = make_bag(self.donor, component=self.comp, status="TESTING")
        TestResult.objects.create(bag=bag, test_type=TestType.objects.get(name="HIV"),
                                  result_status=TestResult.ResultStatus.NON_REACTIVE,
                                  performed_by=self.staff)
        with self.assertRaises(InventoryError):
            InventoryService.transition(bag.pk, "AVAILABLE", actor=self.staff)

    def test_reactive_result_blocks_release(self):
        make_test_type("HIV")
        bag = make_bag(self.donor, component=self.comp, status="TESTING")
        tr = TestResult.objects.create(
            bag=bag, test_type=TestType.objects.get(name="HIV"),
            result_status=TestResult.ResultStatus.REACTIVE, performed_by=self.staff)
        tr.verified_by = self.staff
        tr.save(update_fields=["verified_by"])
        with self.assertRaises(InventoryError):
            InventoryService.transition(bag.pk, "AVAILABLE", actor=self.staff)

    def test_fully_verified_non_reactive_allows_release(self):
        make_test_type("HIV")
        bag = make_bag(self.donor, component=self.comp, status="TESTING")
        complete_tests_for(bag, self.staff)
        bag = InventoryService.transition(bag.pk, "AVAILABLE", actor=self.staff, reason="ok")
        self.assertEqual(bag.status, "AVAILABLE")
        self.assertEqual(bag.released_by, self.staff)


class ExpiryTests(TestCase):
    def setUp(self):
        self.staff = make_user("inv-staff3", role="STAFF")

    def test_expired_bag_cannot_enter_available_stock(self):
        bag = make_bag(status="TESTING", expires_in_days=-1)
        complete_tests_for(bag, self.staff)
        with self.assertRaises(InventoryError):
            InventoryService.transition(bag.pk, "AVAILABLE", actor=self.staff)

    def test_expire_bags_moves_only_expired_usable_stock(self):
        make_test_type("T")
        expired = make_bag(status="AVAILABLE", expires_in_days=-2)
        fresh = make_bag(status="AVAILABLE", expires_in_days=10)
        moved = InventoryService.expire_bags(actor=self.staff)
        self.assertIn(expired, moved)
        expired.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(expired.status, "EXPIRED")
        self.assertEqual(fresh.status, "AVAILABLE")


class CompatibilityEngineTests(TestCase):
    def setUp(self):
        self.o_neg = make_blood_type("O", "NEG")
        self.a_pos = make_blood_type("A", "POS")
        self.prbc = make_component("prbcx", "PRBCX")

    def test_unconfigured_means_not_compatible(self):
        # No rules at all -> nothing may be considered compatible (anti-assumption).
        allowed, _ = CompatibilityService.compatible_donor_types(self.a_pos, self.prbc)
        self.assertEqual(allowed, set())
        self.assertFalse(CompatibilityService.is_compatible(self.a_pos, self.o_neg, self.prbc))

    def test_rule_grants_compatibility(self):
        make_compat_rule(self.a_pos, self.o_neg, component=self.prbc)
        self.assertTrue(CompatibilityService.is_compatible(self.a_pos, self.o_neg, self.prbc))

    def test_disallowed_rule_not_compatible(self):
        make_compat_rule(self.a_pos, self.a_pos, allowed=False)
        self.assertFalse(CompatibilityService.is_compatible(self.a_pos, self.a_pos))

    def test_component_specific_rule_limits_scope(self):
        wb = make_component("wbx", "WBX")
        make_compat_rule(self.a_pos, self.o_neg, component=self.prbc)
        # A PRBC-scoped rule applies only to PRBC queries — and a component=None
        # lookup matches only component-agnostic rules. It never leaks across.
        self.assertTrue(CompatibilityService.is_compatible(self.a_pos, self.o_neg, self.prbc))
        self.assertFalse(CompatibilityService.is_compatible(self.a_pos, self.o_neg, wb))
        self.assertFalse(CompatibilityService.is_compatible(self.a_pos, self.o_neg))

    def test_inactive_rule_ignored(self):
        make_compat_rule(self.a_pos, self.o_neg, active=False)
        self.assertFalse(CompatibilityService.is_compatible(self.a_pos, self.o_neg))


class BagCodeTests(TestCase):
    def test_unique_auto_codes(self):
        a = make_bag()
        b = make_bag()
        self.assertNotEqual(a.bag_code, b.bag_code)
        self.assertTrue(a.bag_code.startswith("BB-"))
