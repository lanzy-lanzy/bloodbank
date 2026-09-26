"""Donations: lifecycle machine, collection atomicity, guarded transitions."""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.testing import make_component, make_donor, make_donation, make_user
from donations.models import Donation
from donations.services import DonationError, DonationService


class DonationTransitionTests(TestCase):
    def setUp(self):
        self.staff = make_user("don-staff", role="STAFF")
        self.donor = make_donor()

    def test_illegal_transition_raises(self):
        d = make_donation(self.donor, status="REGISTERED")
        with self.assertRaises(DonationError):
            DonationService.transition(d, "COLLECTED", actor=self.staff)

    def test_happy_path_chain(self):
        d = make_donation(self.donor, status="REGISTERED")
        for status in ("SCREENING", "APPROVED"):
            d = DonationService.transition(d, status, actor=self.staff)
        self.assertEqual(d.status, "APPROVED")

    def test_terminal_status_has_no_moves(self):
        d = make_donation(self.donor, status="REJECTED")
        self.assertEqual(Donation.TRANSITIONS["REJECTED"], [])
        with self.assertRaises(DonationError):
            DonationService.transition(d, "SCREENING", actor=self.staff)


class CollectionTests(TestCase):
    def setUp(self):
        self.staff = make_user("don-staff2", role="STAFF")
        self.donor = make_donor()
        self.comp = make_component("wb", "Whole Blood", shelf_life=21)

    def test_collection_requires_approved(self):
        d = make_donation(self.donor, status="SCREENING")
        with self.assertRaises(DonationError):
            DonationService.record_collection(d, component=self.comp, volume_ml=450,
                                              actor=self.staff)

    def test_collection_creates_quarantined_bag(self):
        d = DonationService.create(donor=self.donor, actor=self.staff)
        DonationService.transition(d, "SCREENING", actor=self.staff)
        DonationService.transition(d, "APPROVED", actor=self.staff)
        donation, bag = DonationService.record_collection(
            d, component=self.comp, volume_ml=450, collected_at=timezone.now(),
            actor=self.staff)
        self.assertEqual(donation.status, "COLLECTED")
        self.assertEqual(bag.status, "QUARANTINED")  # never auto-available
        self.assertEqual(bag.donor, self.donor)
        self.assertEqual(donation.volume_ml, 450)

    def test_collection_closes_linked_appointment(self):
        from appointments.models import Appointment
        appt = Appointment.objects.create(donor=self.donor, date=timezone.localdate(),
                                          time=timezone.now().time(), status="CHECKED_IN")
        d = DonationService.create(donor=self.donor, actor=self.staff, appointment=appt)
        DonationService.transition(d, "SCREENING", actor=self.staff)
        DonationService.transition(d, "APPROVED", actor=self.staff)
        DonationService.record_collection(d, component=self.comp, volume_ml=450,
                                          collected_at=timezone.now(), actor=self.staff)
        appt.refresh_from_db()
        self.assertEqual(appt.status, "COMPLETED")


class DonationViewGuardTests(TestCase):
    def setUp(self):
        from django.test import Client
        self.client = Client()
        self.staff = make_user("don-staff3", role="STAFF")
        self.client.login(username="don-staff3", password="Test12345!")
        self.donor = make_donor()

    def test_deferral_without_reason_is_refused(self):
        d = make_donation(self.donor, status="REGISTERED")
        resp = self.client.post(reverse("donations:transition", kwargs={"pk": d.pk}),
                                {"status": "DEFERRED", "reason": ""}, follow=True)
        d.refresh_from_db()
        self.assertEqual(d.status, "REGISTERED")
        self.assertContains(resp, "reason is required", status_code=200)

    def test_deferral_with_reason_succeeds(self):
        d = make_donation(self.donor, status="REGISTERED")
        self.client.post(reverse("donations:transition", kwargs={"pk": d.pk}),
                         {"status": "DEFERRED", "reason": "low hb"})
        d.refresh_from_db()
        self.assertEqual(d.status, "DEFERRED")
