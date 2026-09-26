"""Appointments: slot conflicts, self-booking rules, workflow gating."""
from datetime import time as dtime, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment
from core.testing import make_donor, make_user


class ConflictTests(TestCase):
    def setUp(self):
        self.donor = make_donor()

    def test_open_duplicate_slot_conflicts(self):
        Appointment.objects.create(donor=self.donor, date=timezone.localdate(),
                                   time=dtime(9, 0), status="CONFIRMED")
        self.assertTrue(Appointment.has_conflict(self.donor, timezone.localdate(), dtime(9, 0)))

    def test_closed_statuses_free_the_slot(self):
        Appointment.objects.create(donor=self.donor, date=timezone.localdate(),
                                   time=dtime(9, 0), status="CANCELLED")
        self.assertFalse(Appointment.has_conflict(self.donor, timezone.localdate(), dtime(9, 0)))

    def test_unique_constraint_blocks_race_duplicates(self):
        from django.db import IntegrityError, transaction
        Appointment.objects.create(donor=self.donor, date=timezone.localdate(),
                                   time=dtime(10, 0), status="REQUESTED")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Appointment.objects.create(donor=self.donor, date=timezone.localdate(),
                                       time=dtime(10, 0), status="CONFIRMED")

    def test_past_open_appointment_invalid(self):
        a = Appointment(donor=self.donor, date=timezone.localdate() - timedelta(days=2),
                        time=dtime(9, 0), status="REQUESTED")
        with self.assertRaises(ValidationError):
            a.full_clean()


class SelfBookingTests(TestCase):
    def setUp(self):
        from django.test import Client
        user = make_user("appt-donor", role="DONOR")
        self.donor = make_donor(user=user)
        self.client = Client()
        self.client.login(username="appt-donor", password="Test12345!")

    def _post(self, date):
        return self.client.post(reverse("appointments:my_appointments"),
                                {"date": date, "time": "09:30",
                                 "location": "Drive", "notes": ""})

    def test_future_booking_forced_to_requested(self):
        future = (timezone.localdate() + timedelta(days=3)).isoformat()
        self._post(future)
        appt = Appointment.objects.get(donor=self.donor)
        self.assertEqual(appt.status, "REQUESTED")  # never auto-confirmed

    def test_past_booking_rejected(self):
        past = (timezone.localdate() - timedelta(days=1)).isoformat()
        self._post(past)
        self.assertFalse(Appointment.objects.filter(donor=self.donor).exists())

    def test_donor_cannot_open_staff_list(self):
        resp = self.client.get(reverse("appointments:list"))
        self.assertIn(resp.status_code, (403, 302))
