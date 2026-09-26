"""Requests: lifecycle, organization scoping, allocation safety, fulfillment."""
from django.test import TestCase
from django.urls import reverse

from core.testing import (make_bag, make_blood_type, make_compat_rule, make_component,
                          make_donor, make_org, make_item, make_request, make_requester,
                          make_user)
from requests.models import Allocation, BloodRequest
from requests.services import BloodRequestService, RequestError


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
