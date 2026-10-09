"""Inventory: state machine, release guard, expiry, compatibility engine."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from core.testing import (make_bag, make_blood_type, make_component, make_compat_rule,
                          make_donor, make_item, make_org, make_request, make_test_type,
                          make_user)
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


class DemandPanelTests(TestCase):
    """Open demand is reported against compatible stock, never against guesses."""

    def setUp(self):
        self.staff = make_user("dem-staff", role="STAFF")
        self.org = make_org("Demand Hosp")
        self.bt = make_blood_type("A", "POS")
        self.comp = make_component("prbc", "Packed Red Cells")
        self.donor = make_donor(blood_type=self.bt)
        make_compat_rule(self.bt, self.bt, component=self.comp)

    def _approved(self, quantity=2):
        from requests.services import BloodRequestService
        request = make_request(self.org, status="SUBMITTED")
        item = make_item(request, self.bt, self.comp, quantity=quantity)
        BloodRequestService.start_review(request, actor=self.staff)
        BloodRequestService.approve(request, actor=self.staff)
        return request, item

    def _demand(self):
        from requests.services import BloodRequestService
        return BloodRequestService.open_demand()

    def test_shortage_reported_when_stock_is_absent(self):
        request, _ = self._approved(quantity=2)
        rows = self._demand()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["outstanding"], rows[0]["compatible"], rows[0]["shortage"]), (2, 0, 2))
        self.assertEqual(rows[0]["request"], request)

    def test_unapproved_requests_are_not_demand(self):
        make_item(make_request(self.org, status="SUBMITTED"), self.bt, self.comp)
        self.assertEqual(self._demand(), [])

    def test_reserved_bags_count_as_in_flight_not_as_stock(self):
        from requests.services import BloodRequestService
        request, item = self._approved(quantity=2)
        bag = make_bag(self.donor, blood_type=self.bt, component=self.comp,
                       status="AVAILABLE", expires_in_days=20)
        BloodRequestService.allocate_bag(item, bag, actor=self.staff)
        row = self._demand()[0]
        # 2 still owed, 1 already held for it, so only 1 unit needs free stock —
        # and the reserved bag is no longer free, leaving a shortage of 1.
        self.assertEqual((row["outstanding"], row["in_flight"], row["reservable"]), (2, 1, 1))
        self.assertEqual((row["compatible"], row["shortage"]), (0, 1))
        # Adding a second, still-free bag clears the shortage without touching
        # the reservation already in place.
        make_bag(self.donor, blood_type=self.bt, component=self.comp,
                 status="AVAILABLE", expires_in_days=20)
        row = self._demand()[0]
        self.assertEqual((row["compatible"], row["shortage"]), (1, 0))

    def test_fully_covered_item_leaves_the_panel(self):
        from requests.services import BloodRequestService
        _, item = self._approved(quantity=1)
        bag = make_bag(self.donor, blood_type=self.bt, component=self.comp,
                       status="AVAILABLE", expires_in_days=20)
        allocation = BloodRequestService.allocate_bag(item, bag, actor=self.staff)
        BloodRequestService.issue_allocation(allocation, actor=self.staff)
        self.assertEqual(self._demand(), [])

    def test_unconfigured_rules_are_flagged_not_guessed(self):
        other_type = make_blood_type("B", "NEG")
        other_comp = make_component("ffp", "Fresh Frozen Plasma")
        request = make_request(self.org, status="SUBMITTED")
        make_item(request, other_type, other_comp)
        from requests.services import BloodRequestService
        BloodRequestService.start_review(request, actor=self.staff)
        BloodRequestService.approve(request, actor=self.staff)
        row = self._demand()[0]
        self.assertFalse(row["rules_configured"])
        self.assertEqual(row["shortage"], 1)

    def test_dashboard_renders_the_demand_table(self):
        from django.urls import reverse
        request, _ = self._approved(quantity=2)
        self.client.force_login(make_user("dem-admin", role="ADMIN"))
        resp = self.client.get(reverse("inventory:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Open Demand vs Stock on Hand")
        self.assertContains(resp, request.request_code)
        self.assertContains(resp, "Short 2 unit(s)")


class StockAttentionTests(TestCase):
    """Expiry window, stale statuses and the sidebar count share one definition."""

    def setUp(self):
        self.bt = make_blood_type("AB", "POS")
        self.comp = make_component("wb", "Whole Blood")
        self.donor = make_donor(blood_type=self.bt)

    def test_alert_window_follows_the_setting(self):
        from core.testing import set_rules
        make_bag(self.donor, blood_type=self.bt, component=self.comp,
                 status="AVAILABLE", expires_in_days=3)
        set_rules(expiring_soon_days=1)
        self.assertEqual(InventoryService.expiring_soon().count(), 0)
        set_rules(expiring_soon_days=7)
        self.assertEqual(InventoryService.expiring_soon().count(), 1)

    def test_usable_past_expiry_surfaces_stale_status(self):
        make_bag(self.donor, blood_type=self.bt, component=self.comp,
                 status="AVAILABLE", expires_in_days=-2)
        stale = InventoryService.usable_past_expiry()
        self.assertEqual(stale.count(), 1)
        # Such a bag is never offered as compatible stock.
        bags, shortage = CompatibilityService.find_compatible_inventory(self.bt, self.comp, 1)
        self.assertEqual((bags, shortage), ([], 1))

    def test_attention_count_combines_expiring_stale_and_shortage(self):
        from core.testing import set_rules
        from requests.services import BloodRequestService
        set_rules(expiring_soon_days=7)
        staff = make_user("att-staff", role="STAFF")
        org = make_org("Attention Hosp")
        make_compat_rule(self.bt, self.bt, component=self.comp)
        make_bag(self.donor, blood_type=self.bt, component=self.comp,
                 status="AVAILABLE", expires_in_days=2)                       # expiring soon
        make_bag(self.donor, blood_type=self.bt, component=self.comp,
                 status="QUARANTINED", expires_in_days=-1)                     # stale status
        request = make_request(org, status="SUBMITTED")
        make_item(request, self.bt, self.comp, quantity=5)                     # shortage of 4
        BloodRequestService.start_review(request, actor=staff)
        BloodRequestService.approve(request, actor=staff)
        self.assertEqual(InventoryService.attention_count(), 3)

    def test_dashboard_warns_about_stale_usable_bags(self):
        from django.urls import reverse
        make_bag(self.donor, blood_type=self.bt, component=self.comp,
                 status="AVAILABLE", expires_in_days=-1)
        self.client.force_login(make_user("att-admin2", role="ADMIN"))
        resp = self.client.get(reverse("inventory:dashboard"))
        self.assertContains(resp, "past their expiration date but still sit in a")
        self.assertContains(resp, "expiry=expired")

    def test_dashboard_omits_the_stale_warning_when_stock_is_clean(self):
        from django.urls import reverse
        self.client.force_login(make_user("att-admin3", role="ADMIN"))
        self.assertNotContains(self.client.get(reverse("inventory:dashboard")),
                              "past their expiration date but still sit in a")

    def test_badge_endpoint_and_permissions(self):
        from django.urls import reverse
        make_bag(self.donor, blood_type=self.bt, component=self.comp,
                 status="AVAILABLE", expires_in_days=1)
        self.client.force_login(make_user("att-admin", role="ADMIN"))
        self.assertContains(self.client.get(reverse("inventory:badge")), ">1<")
        self.client.force_login(make_user("att-donor", role="DONOR"))
        self.assertIn(self.client.get(reverse("inventory:badge")).status_code, (302, 403))


class BagListFilterTests(TestCase):
    def setUp(self):
        self.staff = make_user("filter-staff", role="STAFF")
        self.bt = make_blood_type("O", "POS")
        self.comp = make_component("wb", "Whole Blood")
        self.donor = make_donor(blood_type=self.bt)
        self.quarantined = make_bag(self.donor, blood_type=self.bt, component=self.comp,
                                    status="QUARANTINED", expires_in_days=20)
        self.available = make_bag(self.donor, blood_type=self.bt, component=self.comp,
                                  status="AVAILABLE", expires_in_days=20)
        self.stale = make_bag(self.donor, blood_type=self.bt, component=self.comp,
                              status="AVAILABLE", expires_in_days=-1)

    def _codes(self, query):
        from django.urls import reverse
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("inventory:bag_list"), query)
        self.assertEqual(resp.status_code, 200)
        return {bag.bag_code for bag in resp.context["bags"]}

    def test_comma_separated_statuses_filter_together(self):
        testing = make_bag(self.donor, blood_type=self.bt, component=self.comp,
                           status="TESTING", expires_in_days=20)
        codes = self._codes({"status": "QUARANTINED,TESTING"})
        self.assertIn(self.quarantined.bag_code, codes)
        self.assertIn(testing.bag_code, codes)
        self.assertNotIn(self.available.bag_code, codes)

    def test_in_date_filter_drops_expired_stock(self):
        codes = self._codes({"status": "AVAILABLE", "expiry": "active"})
        self.assertEqual(codes, {self.available.bag_code})

    def test_expired_status_value_is_not_treated_as_a_status(self):
        # A garbage status must not widen the result set to everything.
        codes = self._codes({"status": "NOPE"})
        self.assertEqual(codes, {self.quarantined.bag_code, self.available.bag_code,
                                 self.stale.bag_code})

    def test_table_names_the_owning_request(self):
        from django.urls import reverse
        from requests.services import BloodRequestService
        make_compat_rule(self.bt, self.bt, component=self.comp)
        org = make_org("Bag List Hosp")
        request = make_request(org, status="SUBMITTED")
        item = make_item(request, self.bt, self.comp)
        BloodRequestService.start_review(request, actor=self.staff)
        BloodRequestService.approve(request, actor=self.staff)
        BloodRequestService.allocate_bag(item, self.available, actor=self.staff)
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("inventory:bag_list"), {"status": "RESERVED"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, request.request_code)
        self.assertContains(resp, "Allocated to")


class BagDetailIsAPageTests(TestCase):
    """The bag record is a working surface, so it is a PAGE and never a modal.

    It carries the record-test-result form, second-person verification and the
    guarded release/transition actions. Packed into a modal those live in a
    nested scroll box, the stock context around them disappears, and a stray
    click discards half-entered work — so this locks the decision in.
    """

    def setUp(self):
        from django.urls import reverse
        self.reverse = reverse
        self.staff = make_user("bag-page-staff", role="STAFF")
        self.donor_user = make_user("bag-page-donor", role="DONOR")
        make_donor(user=self.donor_user)
        self.bt = make_blood_type("O", "NEG")
        self.comp = make_component("wb", "Whole Blood", shelf_life=30)
        self.bag = make_bag(make_donor(blood_type=self.bt), blood_type=self.bt,
                            component=self.comp, status="QUARANTINED")
        self.client.force_login(self.staff)

    def test_htmx_request_still_gets_a_full_page_not_a_modal_fragment(self):
        # The regression this guards: an hx-get to #modal-root that silently
        # half-renders. A full document here means such a link would break
        # loudly instead of quietly.
        resp = self.client.get(self.reverse("inventory:bag_detail", kwargs={"pk": self.bag.pk}),
                               HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("<html", body)
        self.assertIn("</html>", body)
        # The modal shell is what `layout` swaps the template into; it must not
        # be here. (Links to *other*, lighter screens - donor, donation,
        # request - may still open modals from this page; that is fine.)
        self.assertNotIn("bbModal()", body)
        self.assertNotIn('hx-get="/inventory/bags/%d/"' % self.bag.pk, body)

    def test_page_carries_the_working_surfaces(self):
        body = self.client.get(
            self.reverse("inventory:bag_detail", kwargs={"pk": self.bag.pk})).content.decode()
        self.assertIn("Record Test Result", body)
        self.assertIn("Other Transitions", body)
        self.assertIn("Movement Ledger", body)
        self.assertIn("Back to blood bags", body)

    def test_forms_are_plain_posts_not_modal_posts(self):
        body = self.client.get(
            self.reverse("inventory:bag_detail", kwargs={"pk": self.bag.pk})).content.decode()
        self.assertIn('action="/inventory/bags/%d/test/"' % self.bag.pk, body)
        self.assertNotIn('hx-post="/inventory/bags/%d/test/"' % self.bag.pk, body)

    def test_every_bag_link_is_a_plain_anchor(self):
        """No entry point may point a bag link at the modal root again.

        Scans the template sources rather than rendering each one: rendering
        them all would need a full context per app, and the thing being guarded
        is a static attribute on the anchor, not a rendered value.
        """
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent / "templates"
        offenders = []
        for path in root.rglob("*.html"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if "inventory:bag_detail" in line and "hx-get" in line:
                    offenders.append(f"{path.name}: {line.strip()[:120]}")
        self.assertEqual(offenders, [],
                         f"bag detail re-opened as a modal: {offenders}")

    def test_still_staff_only(self):
        self.client.force_login(self.donor_user)
        self.assertEqual(
            self.client.get(
                self.reverse("inventory:bag_detail", kwargs={"pk": self.bag.pk})).status_code, 403)


class InventoryStatementTests(TestCase):
    """The printable Inventory Statement: formal, filtered, and staff-only.

    It must be a faithful snapshot of the same numbers the dashboard shows, so
    these tests compare the two rather than just checking a 200.
    """

    def setUp(self):
        from django.urls import reverse
        self.reverse = reverse
        self.staff = make_user("inv-stmt-staff", role="STAFF")
        self.requester_user = make_user("inv-stmt-req", role="REQUESTER")
        self.donor_user = make_user("inv-stmt-donor", role="DONOR")
        make_org("Statement Org")
        make_donor(user=self.donor_user)
        self.bt = make_blood_type("O", "NEG")
        self.comp = make_component("wb", "Whole Blood", shelf_life=35)
        self.donor = make_donor(blood_type=self.bt)
        self.available = make_bag(self.donor, blood_type=self.bt, component=self.comp,
                                  status="AVAILABLE", expires_in_days=20)
        self.quarantined = make_bag(self.donor, blood_type=self.bt, component=self.comp,
                                    status="QUARANTINED", expires_in_days=3)
        self.expired = make_bag(self.donor, blood_type=self.bt, component=self.comp,
                                status="EXPIRED", expires_in_days=-4)
        self.client.force_login(self.staff)

    def test_statement_preview_renders(self):
        resp = self.client.get(self.reverse("inventory:statement"))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Inventory Statement", body)
        self.assertIn("Blood Bank Management System", body)
        self.assertIn("CONFIDENTIAL", body)
        # Signature block: a statement gets signed.
        self.assertIn("Prepared by", body)
        self.assertIn("Approved by", body)

    def test_statement_lists_every_bag_with_its_expiry_and_status(self):
        body = self.client.get(self.reverse("inventory:statement")).content.decode()
        for bag in (self.available, self.quarantined, self.expired):
            self.assertIn(bag.bag_code, body)

    def test_statement_respects_the_bag_filters(self):
        resp = self.client.get(self.reverse("inventory:statement"), {"status": "QUARANTINED"})
        body = resp.content.decode()
        self.assertIn(self.quarantined.bag_code, body)
        self.assertNotIn(self.available.bag_code, body)

    def test_statement_counts_agree_with_the_dashboard(self):
        from inventory.services import build_inventory_statement
        spec = build_inventory_statement(self.client.get(self.reverse("inventory:statement")).wsgi_request)
        summary = dict(spec.summary)
        dashboard = self.client.get(self.reverse("inventory:dashboard")).content.decode()
        self.assertEqual(summary["Total bags"], "3")
        self.assertEqual(summary["Available"], "1")
        self.assertEqual(summary["Quarantine / testing"], "1")
        self.assertEqual(summary["Expired"], "1")
        # The dashboard states the same "available" figure, so the two agree.
        self.assertIn("1", dashboard)

    def test_statement_names_the_configured_expiry_window(self):
        from core.testing import set_rules
        set_rules(expiring_soon_days=5)
        body = self.client.get(self.reverse("inventory:statement")).content.decode()
        self.assertIn("5 day", body)

    def test_statement_warns_about_stock_past_its_expiry(self):
        make_bag(self.donor, blood_type=self.bt, component=self.comp,
                 status="AVAILABLE", expires_in_days=-1)
        body = self.client.get(self.reverse("inventory:statement")).content.decode()
        self.assertIn("past their expiration date", body)

    def test_statement_pdf_is_a_valid_download(self):
        resp = self.client.get(self.reverse("inventory:statement_pdf"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertIn("attachment;", resp["Content-Disposition"])
        self.assertRegex(resp["Content-Disposition"], r"inventory_statement_\d{8}_\d{4}\.pdf")
        self.assertTrue(resp.content.startswith(b"%PDF-"))

    def test_statement_is_staff_only(self):
        for user in (self.requester_user, self.donor_user):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(
                    self.client.get(self.reverse("inventory:statement")).status_code, 403)
                self.assertEqual(
                    self.client.get(self.reverse("inventory:statement_pdf")).status_code, 403)
