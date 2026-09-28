"""Requests: lifecycle, organization scoping, allocation safety, fulfillment."""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.testing import (make_bag, make_blood_type, make_compat_rule, make_component,
                          make_donor, make_org, make_item, make_request, make_requester,
                          make_user, set_rules)
from notifications.models import Notification, NotificationTemplate
from requests.forms import AllocateForm
from requests.models import Allocation, BloodRequest
from requests.services import BloodRequestService, RequestError


def make_request_templates():
    """Tests run on an unseeded DB, so the request templates the service sends
    by code must exist inline (seed_demo keeps the identical codes)."""
    NotificationTemplate.objects.bulk_create([
        NotificationTemplate(name="Approved", code="request_approved",
                             subject="Request {{ request_id }} approved",
                             body="Approved for {{ organization }}.", channels="in_app"),
        NotificationTemplate(name="Rejected", code="request_rejected",
                             subject="Request {{ request_id }} not approved",
                             body="Reason: {{ rejection_reason }}", channels="in_app"),
        NotificationTemplate(name="Fulfilled", code="request_fulfilled",
                             subject="Request {{ request_id }} fulfilled",
                             body="Bags released to {{ organization }}.", channels="in_app"),
        NotificationTemplate(name="Emergency", code="emergency_request_alert",
                             subject="Emergency {{ request_id }} ({{ urgency }})",
                             body="Emergency request for {{ organization }}.", channels="in_app"),
    ])


class LifecycleTests(TestCase):
    def setUp(self):
        self.staff = make_user("req-staff", role="STAFF")
        self.org = make_org()
        self.bt = make_blood_type("O", "NEG")
        self.comp = make_component("wb", "Whole Blood")

    def test_submit_requires_items(self):
        r = make_request(self.org, status="DRAFT")
        with self.assertRaises(RequestError):
            BloodRequestService.submit(r, actor=self.staff)

    def test_submit_moves_draft_to_submitted(self):
        r = make_request(self.org, status="DRAFT", created_by=self.staff)
        make_item(r, self.bt, self.comp)
        BloodRequestService.submit(r, actor=self.staff)
        r.refresh_from_db()
        self.assertEqual(r.status, "SUBMITTED")

    def test_review_then_approve(self):
        r = make_request(self.org)
        BloodRequestService.start_review(r, actor=self.staff)
        r.refresh_from_db()
        self.assertEqual(r.status, "UNDER_REVIEW")
        BloodRequestService.approve(r, actor=self.staff)
        r.refresh_from_db()
        self.assertEqual(r.status, "APPROVED")

    def test_reject_requires_later_status_moves_fail(self):
        r = make_request(self.org)
        BloodRequestService.reject(r, reason="incomplete", actor=self.staff)
        r.refresh_from_db()
        self.assertEqual(r.status, "REJECTED")
        with self.assertRaises(RequestError):
            BloodRequestService.approve(r, actor=self.staff)


class SidebarBadgeTests(TestCase):
    """The Blood Requests pill counts work waiting on the viewer's side."""

    def setUp(self):
        self.staff = make_user("badge-staff", role="STAFF")
        self.org = make_org("Badge Hosp")
        self.bt = make_blood_type("B", "POS")
        self.comp = make_component("wb", "Whole Blood")

    def _request(self, status, quantity=2):
        r = make_request(self.org, status=status)
        make_item(r, self.bt, self.comp, quantity=quantity)
        return r

    def test_staff_count_covers_decisions_and_outstanding_units(self):
        self._request("SUBMITTED")
        self._request("UNDER_REVIEW")
        self._request("APPROVED")
        self._request("DRAFT")      # requester still drafting — not staff work
        self._request("REJECTED")
        self._request("EXPIRED")
        self.assertEqual(BloodRequestService.awaiting_action_count(self.staff), 3)

    def test_partially_fulfilled_with_units_owed_counts(self):
        r = self._request("PARTIALLY_FULFILLED", quantity=3)
        r.items.update(fulfilled_quantity=1)
        self.assertEqual(BloodRequestService.awaiting_action_count(self.staff), 1)

    def test_request_needs_nothing_once_units_are_covered(self):
        r = self._request("APPROVED")
        r.items.update(fulfilled_quantity=2)
        self.assertEqual(BloodRequestService.awaiting_action_count(self.staff), 0)

    def test_multi_item_request_counts_once(self):
        r = make_request(self.org, status="SUBMITTED")
        make_item(r, self.bt, self.comp, quantity=2)
        make_item(r, self.bt, make_component("prbc", "Packed Red Cells"), quantity=1)
        self.assertEqual(BloodRequestService.awaiting_action_count(self.staff), 1)

    def test_requester_counts_only_own_open_requests(self):
        self._request("SUBMITTED")
        self._request("FULFILLED")
        other = make_request(make_org("Other Hosp"), status="APPROVED")
        make_item(other, self.bt, self.comp)
        requester = make_requester("badge-req", org=self.org)
        self.assertEqual(BloodRequestService.awaiting_action_count(requester), 1)

    def test_unlinked_requester_counts_zero(self):
        from accounts.models import User
        user = make_user("badge-nolink", role="REQUESTER")
        user.refresh_from_db()
        self.assertIsNone(getattr(user, "requester_profile", None))
        self.assertEqual(BloodRequestService.awaiting_action_count(user), 0)

    def test_endpoint_renders_pill_for_staff(self):
        self._request("SUBMITTED")
        self.client.login(username="badge-staff", password="Test12345!")
        resp = self.client.get(reverse("requests:badge"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, ">1<")

    def test_endpoint_denies_donor(self):
        make_user("badge-donor", role="DONOR")
        self.client.login(username="badge-donor", password="Test12345!")
        self.assertEqual(self.client.get(reverse("requests:badge")).status_code, 403)


class AllocationSafetyTests(TestCase):
    def setUp(self):
        self.staff = make_user("req-staff2", role="STAFF")
        self.org = make_org("Alloc Hosp")
        self.bt = make_blood_type("A", "POS")
        self.donor_bt = make_blood_type("A", "POS")
        self.comp = make_component("wb", "Whole Blood")
        self.donor = make_donor(blood_type=self.donor_bt)
        self.request = make_request(self.org)
        self.item = make_item(self.request, self.bt, self.comp)
        make_compat_rule(self.bt, self.donor_bt, component=self.comp)

    def _reserve(self):
        bag = make_bag(self.donor, blood_type=self.donor_bt, component=self.comp,
                       status="AVAILABLE")
        BloodRequestService.approve(self.request, actor=self.staff)
        return bag

    def test_allocate_requires_approved_request(self):
        bag = make_bag(self.donor, blood_type=self.donor_bt, component=self.comp,
                       status="AVAILABLE")
        with self.assertRaises(RequestError):
            BloodRequestService.allocate_bag(self.item, bag, actor=self.staff)

    def test_incompatible_bag_refused(self):
        b_neg = make_blood_type("B", "NEG")
        wrong = make_bag(self.donor, blood_type=b_neg, component=self.comp, status="AVAILABLE")
        BloodRequestService.approve(self.request, actor=self.staff)
        with self.assertRaises(RequestError):
            BloodRequestService.allocate_bag(self.item, wrong, actor=self.staff)

    def test_allocate_reserves_bag_and_blocks_second_allocation(self):
        bag = self._reserve()
        alloc = BloodRequestService.allocate_bag(self.item, bag, actor=self.staff)
        bag.refresh_from_db()
        self.assertEqual(bag.status, "RESERVED")
        self.assertEqual(alloc.status, Allocation.Status.RESERVED)
        with self.assertRaises(RequestError):  # bag no longer AVAILABLE (one active allocation per bag)
            BloodRequestService.allocate_bag(self.item, bag, actor=self.staff)

    def test_issue_updates_counters_and_transfuse_moves_bag(self):
        bag = self._reserve()
        alloc = BloodRequestService.allocate_bag(self.item, bag, actor=self.staff)
        BloodRequestService.issue_allocation(alloc, actor=self.staff)
        alloc.refresh_from_db()
        bag.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(alloc.status, Allocation.Status.ISSUED)
        self.assertEqual(bag.status, "ISSUED")
        self.assertEqual(self.item.fulfilled_quantity, 1)
        BloodRequestService.mark_transfused(alloc, actor=self.staff)
        bag.refresh_from_db()
        alloc.refresh_from_db()
        self.assertEqual(bag.status, "TRANSFUSED")
        self.assertEqual(alloc.status, Allocation.Status.ISSUED)  # allocations never claim TRANSFUSED

    def test_cancel_allocation_returns_bag_to_stock(self):
        bag = self._reserve()
        alloc = BloodRequestService.allocate_bag(self.item, bag, actor=self.staff)
        BloodRequestService.cancel_allocation(alloc, actor=self.staff, reason="no longer needed")
        bag.refresh_from_db()
        self.assertEqual(bag.status, "AVAILABLE")


class RequesterScopingTests(TestCase):
    def setUp(self):
        from django.test import Client
        self.client = Client()
        self.requester = make_requester("doc-a")
        self.other_org = make_org("Other Clinic")
        self.org = self.requester.requester_profile.organization
        self.bt = make_blood_type("O", "POS")
        self.comp = make_component("wb", "Whole Blood")

    def test_requester_sees_own_org_request(self):
        r = make_request(self.org, created_by=self.requester)
        self.client.login(username="doc-a", password="Test12345!")
        resp = self.client.get(reverse("requests:detail", kwargs={"pk": r.pk}))
        self.assertEqual(resp.status_code, 200)

    def test_requester_blocked_from_other_org_request(self):
        r = make_request(self.other_org)
        self.client.login(username="doc-a", password="Test12345!")
        resp = self.client.get(reverse("requests:detail", kwargs={"pk": r.pk}))
        self.assertIn(resp.status_code, (403, 404))

    def test_requester_cannot_approve_own_request(self):
        r = make_request(self.org, created_by=self.requester)
        self.client.login(username="doc-a", password="Test12345!")
        resp = self.client.post(reverse("requests:action", kwargs={"pk": r.pk}),
                               {"action": "approve"})
        self.assertIn(resp.status_code, (403, 302))
        r.refresh_from_db()
        self.assertNotEqual(r.status, "APPROVED")


class RequesterNotificationTests(TestCase):
    """Approve/reject must reach the requesting organization, not just fulfill."""

    def setUp(self):
        make_request_templates()
        self.staff = make_user("notif-staff", role="STAFF")
        self.requester = make_requester("notif-doc")
        self.org = self.requester.requester_profile.organization
        self.bt = make_blood_type("AB", "POS")
        self.comp = make_component("wb", "Whole Blood")

    def _request(self):
        r = make_request(self.org, created_by=self.requester)
        make_item(r, self.bt, self.comp)
        return r

    def _for(self, code):
        return Notification.objects.filter(user=self.requester, template__code=code)

    def test_approve_notifies_requester(self):
        r = self._request()
        BloodRequestService.approve(r, actor=self.staff)
        self.assertEqual(self._for("request_approved").count(), 1)
        self.assertIn(r.request_code, self._for("request_approved").first().subject)

    def test_reject_notifies_requester_with_reason(self):
        r = self._request()
        BloodRequestService.reject(r, reason="no matching indication", actor=self.staff)
        notification = self._for("request_rejected").first()
        self.assertIsNotNone(notification)
        self.assertIn("no matching indication", notification.body)

    def test_missing_template_never_blocks_the_decision(self):
        """Fail-safe: notification is a side effect, the approval must stand."""
        NotificationTemplate.objects.filter(code="request_approved").delete()
        r = self._request()
        BloodRequestService.approve(r, actor=self.staff)
        r.refresh_from_db()
        self.assertEqual(r.status, BloodRequest.Status.APPROVED)
        self.assertEqual(self._for("request_approved").count(), 0)

    def test_draft_without_created_by_notifies_nobody(self):
        r = make_request(self.org)          # created_by is None
        make_item(r, self.bt, self.comp)
        BloodRequestService.approve(r, actor=self.staff)
        self.assertEqual(Notification.objects.count(), 0)


class AllocateFormStockTests(TestCase):
    """The dropdown may only offer bags the Compatibility Report counts."""

    def setUp(self):
        self.staff = make_user("alloc-staff", role="STAFF")
        self.org = make_org("Alloc Form Hosp")
        self.bt = make_blood_type("O", "POS")
        self.comp = make_component("wb", "Whole Blood")
        self.donor = make_donor(blood_type=self.bt)
        self.request = make_request(self.org)
        self.item = make_item(self.request, self.bt, self.comp)
        make_compat_rule(self.bt, self.bt, component=self.comp)

    def _form(self):
        return AllocateForm(item=self.item)

    def test_expired_bag_excluded_from_choices(self):
        fresh = make_bag(self.donor, blood_type=self.bt, component=self.comp,
                         status="AVAILABLE", expires_in_days=5)
        make_bag(self.donor, blood_type=self.bt, component=self.comp,
                 status="AVAILABLE", expires_in_days=-1)
        ids = list(self._form().fields["bag"].queryset.values_list("pk", flat=True))
        self.assertEqual(ids, [fresh.pk])

    def test_reserved_bag_excluded_from_choices(self):
        bag = make_bag(self.donor, blood_type=self.bt, component=self.comp,
                       status="AVAILABLE", expires_in_days=5)
        BloodRequestService.approve(self.request, actor=self.staff)
        BloodRequestService.allocate_bag(self.item, bag, actor=self.staff)
        self.assertEqual(list(self._form().fields["bag"].queryset), [])


class ShortageDisplayTests(TestCase):
    """Admins see an explicit shortage message instead of an empty select."""

    def setUp(self):
        make_request_templates()
        self.staff = make_user("short-staff", role="STAFF")
        self.org = make_org("Shortage Hosp")
        self.bt = make_blood_type("A", "NEG")
        self.donor_bt = make_blood_type("A", "NEG")
        self.comp = make_component("prbc", "Packed Red Cells")
        self.donor = make_donor(blood_type=self.donor_bt)
        make_compat_rule(self.bt, self.donor_bt, component=self.comp)
        self.client.force_login(self.staff)

    def _approved(self, quantity=2, status="APPROVED"):
        r = make_request(self.org, status="SUBMITTED", created_by=make_requester("short-doc", org=self.org))
        item = make_item(r, self.bt, self.comp, quantity=quantity)
        BloodRequestService.start_review(r, actor=self.staff)
        BloodRequestService.approve(r, actor=self.staff)
        return r, item

    def _detail(self, r):
        resp = self.client.get(reverse("requests:detail", kwargs={"pk": r.pk}))
        self.assertEqual(resp.status_code, 200)
        return resp

    def test_shortage_text_when_no_compatible_stock(self):
        r, _ = self._approved()
        resp = self._detail(r)
        self.assertContains(resp, "No compatible bag in stock")
        self.assertNotContains(resp, "Reserve Bag")

    def test_reserve_offer_with_hint_when_stock_exists(self):
        r, _ = self._approved()
        make_bag(self.donor, blood_type=self.donor_bt, component=self.comp,
                 status="AVAILABLE", expires_in_days=10)
        resp = self._detail(r)
        self.assertContains(resp, "Reserve Bag")
        self.assertContains(resp, "1 compatible bag(s) on hand")
        self.assertNotContains(resp, "No compatible bag in stock")

    def test_allocate_without_a_bag_reports_the_shortage(self):
        r, item = self._approved()
        resp = self.client.post(reverse("requests:action", kwargs={"pk": r.pk}),
                               {"action": "allocate", "item": item.pk})
        self.assertEqual(resp.status_code, 302)
        messages = [m.message for m in resp.wsgi_request._messages]
        self.assertTrue(any("No compatible AVAILABLE bag in stock" in m for m in messages), messages)
        self.assertTrue(any("2 unit(s) still short" in m for m in messages), messages)

    def test_allocate_with_an_unlisted_bag_lists_the_stock(self):
        r, item = self._approved()
        make_bag(self.donor, blood_type=self.donor_bt, component=self.comp,
                 status="AVAILABLE", expires_in_days=10)
        other = make_bag(make_donor(blood_type=self.bt), blood_type=self.bt,
                         component=self.comp, status="EXPIRED")
        resp = self.client.post(reverse("requests:action", kwargs={"pk": r.pk}),
                               {"action": "allocate", "item": item.pk, "bag": other.pk})
        messages = [m.message for m in resp.wsgi_request._messages]
        self.assertTrue(any("Pick one of the 1 compatible bag(s) on hand" in m for m in messages),
                        messages)


class ReservationQuotaTests(TestCase):
    """A reservation consumes the item's quota until it is issued or cancelled.

    Regression cover: outstanding counted only issued units, so two staff could
    each reserve stock for the same unit and the item ended up over-committed.
    """

    def setUp(self):
        self.staff = make_user("quota-staff", role="STAFF")
        self.org = make_org("Quota Hosp")
        self.bt = make_blood_type("O", "NEG")
        self.comp = make_component("wb", "Whole Blood")
        self.donor = make_donor(blood_type=self.bt)
        make_compat_rule(self.bt, self.bt, component=self.comp)
        self.request = make_request(self.org)
        self.item = make_item(self.request, self.bt, self.comp, quantity=1)
        BloodRequestService.start_review(self.request, actor=self.staff)
        BloodRequestService.approve(self.request, actor=self.staff)

    def _bag(self):
        return make_bag(self.donor, blood_type=self.bt, component=self.comp, status="AVAILABLE")

    def _reserve_second_bag(self):
        with self.assertRaises(RequestError):
            BloodRequestService.allocate_bag(self.item, self._bag(), actor=self.staff)

    def test_second_reservation_for_one_unit_is_refused(self):
        BloodRequestService.allocate_bag(self.item, self._bag(), actor=self.staff)
        self.item.refresh_from_db()
        self.assertEqual(self.item.units_reserved, 1)
        self.assertEqual(self.item.reservable_units, 0)
        self.assertEqual(self.item.outstanding, 1)  # the fulfillment view is unchanged
        self._reserve_second_bag()
        self.assertEqual(self.item.request.allocations.filter(status="RESERVED").count(), 1)

    def test_issued_units_also_block_further_reservation(self):
        alloc = BloodRequestService.allocate_bag(self.item, self._bag(), actor=self.staff)
        BloodRequestService.issue_allocation(alloc, actor=self.staff)
        self.item.refresh_from_db()
        self.assertEqual((self.item.reservable_units, self.item.outstanding), (0, 0))
        self._reserve_second_bag()

    def test_cancelling_a_reservation_reopens_the_slot(self):
        alloc = BloodRequestService.allocate_bag(self.item, self._bag(), actor=self.staff)
        BloodRequestService.cancel_allocation(alloc, actor=self.staff, reason="mistake")
        self.item.refresh_from_db()
        self.assertEqual(self.item.reservable_units, 1)
        BloodRequestService.allocate_bag(self.item, self._bag(), actor=self.staff)

    def test_multi_unit_item_holds_up_to_its_quantity(self):
        self.item.quantity = 3
        self.item.save(update_fields=["quantity"])
        for _ in range(3):
            BloodRequestService.allocate_bag(self.item, self._bag(), actor=self.staff)
        self.item.refresh_from_db()
        self.assertEqual((self.item.units_reserved, self.item.reservable_units), (3, 0))
        self._reserve_second_bag()

    def test_detail_page_states_all_units_reserved(self):
        admin = make_user("quota-admin", role="ADMIN")
        BloodRequestService.allocate_bag(self.item, self._bag(), actor=self.staff)
        self.client.force_login(admin)
        resp = self.client.get(reverse("requests:detail", kwargs={"pk": self.request.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "All requested units are reserved")
        self.assertNotContains(resp, "Reserve Bag")


class WalkInRequestTests(TestCase):
    """A patient who comes to the blood bank directly has no requester account,
    so staff log the request against the configured walk-in desk organization."""

    def setUp(self):
        make_request_templates()
        self.staff = make_user("walkin-staff", role="STAFF")
        self.desk = make_org("Walk-in Desk (Blood Bank)")
        self.hospital = make_org("Demo Provincial Hospital")
        self.bt = make_blood_type("O", "POS")
        self.comp = make_component("wb", "Whole Blood")

    def configure_desk(self):
        set_rules(walk_in_organization_id=self.desk.pk)

    def payload(self, **override):
        data = {
            "organization": self.desk.pk,
            "channel": BloodRequest.Channel.WALK_IN,
            "walk_in_contact": "J. Dela Cruz (patient)",
            "walk_in_phone": "09171234567",
            "patient_reference": "PT-WALK-1",
            "required_by": (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
            "urgency": "URGENT",
            "clinical_indication": "",
            "blood_type": self.bt.pk,
            "component": self.comp.pk,
            "quantity": 1,
        }
        data.update(override)
        return data

    def test_desk_comes_from_the_setting_only(self):
        self.assertIsNone(BloodRequestService.walk_in_organization())
        self.configure_desk()
        self.assertEqual(BloodRequestService.walk_in_organization(), self.desk)
        self.desk.is_active = False
        self.desk.save(update_fields=["is_active"])
        self.assertIsNone(BloodRequestService.walk_in_organization())

    def test_staff_can_log_a_walk_in_request(self):
        self.configure_desk()
        self.client.force_login(self.staff)
        resp = self.client.post(reverse("requests:create"), self.payload())
        self.assertEqual(resp.status_code, 302)
        created = BloodRequest.objects.get(organization=self.desk)
        self.assertEqual(created.channel, BloodRequest.Channel.WALK_IN)
        self.assertEqual(created.walk_in_contact, "J. Dela Cruz (patient)")
        self.assertTrue(created.is_walk_in)
        from audit.models import AuditLog
        self.assertIn("walk-in", AuditLog.objects.filter(
            action="REQUEST_CREATED").first().description.lower())

    def test_walk_in_needs_no_approval_and_never_queues(self):
        """Direct clinic request: the counter is the authority, so the record is
        born validated — but still APPROVED, so bag guards stay identical."""
        self.configure_desk()
        self.client.force_login(self.staff)
        self.client.post(reverse("requests:create"), self.payload())
        created = BloodRequest.objects.get(organization=self.desk)
        self.assertEqual(created.status, BloodRequest.Status.APPROVED)
        self.assertEqual(created.approved_by, self.staff)
        self.assertIsNotNone(created.approved_at)
        from audit.models import AuditLog
        validated = AuditLog.objects.filter(action="REQUEST_VALIDATED_AT_COUNTER").first()
        self.assertIn("validated at the counter", validated.description)
        self.assertIn("J. Dela Cruz (patient)", validated.description)

    def test_walk_in_draft_validates_at_the_counter(self):
        self.configure_desk()
        self.client.force_login(self.staff)
        self.client.post(reverse("requests:create"), self.payload(save_draft="1"))
        draft = BloodRequest.objects.get(organization=self.desk)
        self.assertEqual(draft.status, BloodRequest.Status.DRAFT)
        self.client.post(reverse("requests:action", kwargs={"pk": draft.pk}), {"action": "submit"})
        draft.refresh_from_db()
        self.assertEqual(draft.status, BloodRequest.Status.APPROVED)

    def test_walk_in_cannot_be_approved_or_rejected(self):
        """No approval workflow means the actions are refused, not hidden."""
        self.configure_desk()
        walk_in = make_request(self.desk, created_by=self.staff, status=BloodRequest.Status.SUBMITTED,
                               channel=BloodRequest.Channel.WALK_IN,
                               walk_in_contact="J. Dela Cruz (patient)")
        with self.assertRaises(RequestError):
            BloodRequestService.approve(walk_in, actor=self.staff)
        with self.assertRaises(RequestError):
            BloodRequestService.reject(walk_in, reason="not our patient", actor=self.staff)
        walk_in.refresh_from_db()
        self.assertEqual(walk_in.status, BloodRequest.Status.SUBMITTED)

    def test_only_walk_ins_skip_the_approval_queue(self):
        org_request = make_request(self.hospital, created_by=self.staff)
        with self.assertRaises(RequestError):
            BloodRequestService.validate_walk_in(org_request, actor=self.staff)
        self.assertEqual(org_request.status, BloodRequest.Status.SUBMITTED)

    def test_walk_in_sends_no_requester_notification(self):
        self.configure_desk()
        walk_in = make_request(self.desk, created_by=self.staff, status=BloodRequest.Status.SUBMITTED,
                               channel=BloodRequest.Channel.WALK_IN,
                               walk_in_contact="J. Dela Cruz (patient)")
        make_item(walk_in, self.bt, self.comp)
        BloodRequestService.validate_walk_in(walk_in, actor=self.staff)
        from notifications.models import Notification
        self.assertFalse(Notification.objects.exists())
        walk_in.refresh_from_db()
        self.assertEqual(walk_in.status, BloodRequest.Status.APPROVED)

    def test_walk_in_can_still_be_cancelled(self):
        self.configure_desk()
        walk_in = make_request(self.desk, created_by=self.staff, status=BloodRequest.Status.APPROVED,
                               channel=BloodRequest.Channel.WALK_IN,
                               walk_in_contact="J. Dela Cruz (patient)")
        BloodRequestService.cancel(walk_in, reason="Patient transferred to another facility",
                                   actor=self.staff)
        walk_in.refresh_from_db()
        self.assertEqual(walk_in.status, BloodRequest.Status.CANCELLED)

    def test_emergency_walk_in_still_alerts_the_team(self):
        """Nobody outside the counter watched this one get created."""
        self.configure_desk()
        make_user("walkin-admin", role="ADMIN")
        self.client.force_login(self.staff)
        self.client.post(reverse("requests:create"), self.payload(urgency="EMERGENCY"))
        walk_in = BloodRequest.objects.get(organization=self.desk)
        self.assertEqual(walk_in.status, BloodRequest.Status.APPROVED)
        self.assertEqual(Notification.objects.filter(
            template__code="emergency_request_alert").count(), 2)  # staff + admin

    def test_walk_in_detail_shows_no_approve_button(self):
        self.configure_desk()
        walk_in = make_request(self.desk, created_by=self.staff, status=BloodRequest.Status.SUBMITTED,
                               channel=BloodRequest.Channel.WALK_IN,
                               walk_in_contact="J. Dela Cruz (patient)")
        self.client.force_login(self.staff)
        body = self.client.get(reverse("requests:detail", kwargs={"pk": walk_in.pk})).content.decode()
        self.assertNotIn('value="approve"', body)
        self.assertNotIn('value="reject"', body)
        self.assertIn("validated at the counter", body)

    def test_walk_in_is_refused_until_the_desk_is_configured(self):
        self.client.force_login(self.staff)
        resp = self.client.post(reverse("requests:create"), self.payload())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "REQUIRES CONFIGURATION")
        self.assertFalse(BloodRequest.objects.exists())

    def test_walk_in_needs_someone_to_deal_with(self):
        self.configure_desk()
        self.client.force_login(self.staff)
        resp = self.client.post(reverse("requests:create"), self.payload(walk_in_contact="  "))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Required for a walk-in request")
        self.assertFalse(BloodRequest.objects.exists())

    def test_model_requires_a_contact_for_walk_in(self):
        r = make_request(self.desk, created_by=self.staff, channel=BloodRequest.Channel.WALK_IN)
        with self.assertRaises(ValidationError):
            r.full_clean()

    def test_the_desk_only_accepts_walk_in_records(self):
        self.configure_desk()
        self.client.force_login(self.staff)
        resp = self.client.post(reverse("requests:create"),
                                self.payload(channel=BloodRequest.Channel.ORGANIZATION))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "only accepts walk-in records")
        self.assertFalse(BloodRequest.objects.exists())

    def test_requester_cannot_log_a_walk_in(self):
        self.configure_desk()
        requester = make_requester("walkin-doc", org=self.hospital)
        self.client.force_login(requester)
        resp = self.client.post(reverse("requests:create"), self.payload())
        self.assertEqual(resp.status_code, 302)
        created = BloodRequest.objects.get()
        self.assertEqual(created.organization, self.hospital)
        self.assertEqual(created.channel, BloodRequest.Channel.ORGANIZATION)

    def test_walk_in_stays_invisible_to_desk_requester(self):
        self.configure_desk()
        requester = make_requester("desk-doc", org=self.desk)
        walk_in = make_request(self.desk, created_by=self.staff,
                               channel=BloodRequest.Channel.WALK_IN,
                               walk_in_contact="J. Dela Cruz (patient)")
        org_request = make_request(self.desk, created_by=requester)
        self.assertEqual(BloodRequestService.awaiting_action_count(requester), 1)
        self.client.force_login(requester)
        listing = self.client.get(reverse("requests:list"))
        self.assertContains(listing, org_request.request_code)
        self.assertNotContains(listing, walk_in.request_code)
        self.assertIn(self.client.get(reverse("requests:detail", kwargs={"pk": walk_in.pk})).status_code,
                      (302, 403))

    def test_walk_in_entry_point_opens_on_the_walk_in_card(self):
        self.configure_desk()
        self.client.force_login(self.staff)
        body = self.client.get(reverse("requests:create"), {"channel": "WALK_IN"}).content.decode()
        self.assertIn("{ channel: 'WALK_IN' }", body)
        self.assertRegex(body, r'value="WALK_IN"[\s\S]{0,200}checked')
        self.assertIn(self.desk.name, body)

    def test_staff_can_filter_by_channel(self):
        self.configure_desk()
        walk_in = make_request(self.desk, created_by=self.staff,
                               channel=BloodRequest.Channel.WALK_IN,
                               walk_in_contact="J. Dela Cruz (patient)")
        org_request = make_request(self.hospital, created_by=self.staff)
        self.client.force_login(self.staff)
        only_walk_in = self.client.get(reverse("requests:list"), {"channel": "WALK_IN"})
        self.assertContains(only_walk_in, walk_in.request_code)
        self.assertNotContains(only_walk_in, org_request.request_code)
        self.assertContains(only_walk_in, "Walk-in")
        bogus = self.client.get(reverse("requests:list"), {"channel": "NOPE"})
        self.assertContains(bogus, walk_in.request_code)
        self.assertContains(bogus, org_request.request_code)

    def test_staff_close_the_loop_on_an_issued_walk_in(self):
        self.configure_desk()
        make_compat_rule(self.bt, self.bt, component=self.comp)
        r = make_request(self.desk, created_by=self.staff, status="APPROVED",
                         channel=BloodRequest.Channel.WALK_IN,
                         walk_in_contact="J. Dela Cruz (patient)")
        item = make_item(r, self.bt, self.comp, quantity=1)
        bag = make_bag(blood_type=self.bt, component=self.comp, status="AVAILABLE")
        allocation = BloodRequestService.allocate_bag(item, bag, actor=self.staff)
        BloodRequestService.issue_allocation(allocation, actor=self.staff)
        self.client.force_login(self.staff)
        detail = self.client.get(reverse("requests:detail", kwargs={"pk": r.pk}))
        self.assertContains(detail, "Mark Transfused")
        self.assertContains(detail, "logged at the counter with J. Dela Cruz (patient)")
        resp = self.client.post(reverse("requests:allocation_feedback",
                                         kwargs={"pk": r.pk, "allocation_pk": allocation.pk}),
                                {"action": "transfused"})
        self.assertEqual(resp.status_code, 302)
        allocation.refresh_from_db()
        self.assertEqual(allocation.bag.status, "TRANSFUSED")

    def test_walk_in_badge_counts_only_walk_ins_needing_bags(self):
        """A draft or an approved walk-in that still owes units is desk work; a
        fulfilled one is not, and organization requests never count here."""
        self.configure_desk()
        owed = make_request(self.desk, created_by=self.staff, status="APPROVED",
                            channel=BloodRequest.Channel.WALK_IN, walk_in_contact="A. Patient")
        make_item(owed, self.bt, self.comp, quantity=1)
        done = make_request(self.desk, created_by=self.staff, status="FULFILLED",
                            channel=BloodRequest.Channel.WALK_IN, walk_in_contact="B. Patient")
        make_item(done, self.bt, self.comp, quantity=1)
        draft = make_request(self.desk, created_by=self.staff, status="DRAFT",
                             channel=BloodRequest.Channel.WALK_IN, walk_in_contact="C. Patient")
        make_item(draft, self.bt, self.comp, quantity=1)
        make_request(self.hospital, created_by=self.staff)  # organization queue
        self.assertEqual(BloodRequestService.walk_in_action_count(), 2)

    def test_desk_page_lists_the_counter_queue(self):
        self.configure_desk()
        walk_in = make_request(self.desk, created_by=self.staff, status="APPROVED",
                               channel=BloodRequest.Channel.WALK_IN,
                               walk_in_contact="J. Dela Cruz (patient)")
        make_item(walk_in, self.bt, self.comp, quantity=1)
        org_request = make_request(self.hospital, created_by=self.staff)
        self.client.force_login(self.staff)
        desk = self.client.get(reverse("requests:walk_in_desk"))
        self.assertContains(desk, walk_in.request_code)
        self.assertNotContains(desk, org_request.request_code)
        self.assertContains(desk, "Walk-in Desk")
        self.assertContains(desk, "Log Walk-in Request")
        self.assertContains(desk, "J. Dela Cruz (patient)")
        self.assertContains(desk, self.desk.name)

    def test_desk_page_flags_an_unconfigured_desk(self):
        self.client.force_login(self.staff)
        desk = self.client.get(reverse("requests:walk_in_desk"))
        self.assertContains(desk, "REQUIRES CONFIGURATION")
        self.assertContains(desk, "walk_in_organization_id")

    def test_desk_scope_tabs_split_open_from_closed(self):
        self.configure_desk()
        open_one = make_request(self.desk, created_by=self.staff, status="APPROVED",
                                channel=BloodRequest.Channel.WALK_IN, walk_in_contact="A. Patient")
        closed_one = make_request(self.desk, created_by=self.staff, status="CANCELLED",
                                  channel=BloodRequest.Channel.WALK_IN, walk_in_contact="B. Patient")
        self.client.force_login(self.staff)
        default = self.client.get(reverse("requests:walk_in_desk"))
        self.assertContains(default, open_one.request_code)
        self.assertNotContains(default, closed_one.request_code)
        closed = self.client.get(reverse("requests:walk_in_desk"), {"scope": "closed"})
        self.assertContains(closed, closed_one.request_code)
        self.assertNotContains(closed, open_one.request_code)
        everything = self.client.get(reverse("requests:walk_in_desk"), {"scope": "all"})
        self.assertContains(everything, open_one.request_code)
        self.assertContains(everything, closed_one.request_code)

    def test_walk_in_badge_endpoint(self):
        self.configure_desk()
        owed = make_request(self.desk, created_by=self.staff, status="APPROVED",
                            channel=BloodRequest.Channel.WALK_IN, walk_in_contact="A. Patient")
        make_item(owed, self.bt, self.comp, quantity=2)
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("requests:walk_in_badge")), ">1<")

    def test_the_desk_is_staff_only(self):
        self.configure_desk()
        requester = make_requester("desk-doc", org=self.desk)
        donor = make_user("walkin-donor", role="DONOR")
        for user in (requester, donor):
            self.client.force_login(user)
            self.assertIn(self.client.get(reverse("requests:walk_in_desk")).status_code, (302, 403))
            self.assertIn(self.client.get(reverse("requests:walk_in_badge")).status_code, (302, 403))

    def test_sidebar_offers_the_desk_to_staff_only(self):
        self.configure_desk()
        requester = make_requester("desk-doc", org=self.desk)
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("requests:list")), "/requests/walk-ins/")
        self.client.force_login(requester)
        self.assertNotContains(self.client.get(reverse("requests:list")), "/requests/walk-ins/")
