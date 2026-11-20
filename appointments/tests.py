"""Appointments: slot conflicts, self-booking rules, workflow gating."""
from datetime import time as dtime, timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment
from core.testing import make_donor, make_user
from notifications.models import Notification


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


@override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY="test-key")
class ConfirmationSMSTests(TestCase):
    def test_staff_confirms_once_and_sends_to_the_donor_after_commit(self):
        donor = make_donor()
        donor.contact_number = "09171234567"
        donor.save(update_fields=["contact_number"])
        appointment = Appointment.objects.create(
            donor=donor, date=timezone.localdate(), time=dtime(9),
        )
        self.client.force_login(make_user("scheduler"))
        url = reverse("appointments:status", args=[appointment.pk])

        with patch("notifications.providers.SemaphoreSMSProvider._post") as send:
            send.return_value = 200, '[{"message_id": 1, "status": "Queued"}]'
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(url, {"status": "CONFIRMED"})
                send.assert_not_called()
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(url, {"status": "CONFIRMED"})
            send.assert_called_once()
            self.assertEqual(send.call_args.args[0]["number"], donor.contact_number)
        sms = Notification.objects.get(donor=donor, channel="sms")
        self.assertEqual(sms.delivery_status, Notification.DeliveryStatus.SENT)
        self.assertIn("9:00 AM", sms.body)


class CalendarAndBadgeTests(TestCase):
    def setUp(self):
        self.staff = make_user("cal-staff", role="STAFF")
        self.donor_user = make_user("cal-donor", role="DONOR")
        self.donor = make_donor(user=self.donor_user, first_name="Clara", last_name="Diaz")

    def test_calendar_is_default_shows_requested_dates_and_day_panel_via_htmx(self):
        target = timezone.localdate() + timedelta(days=2)
        Appointment.objects.create(
            donor=self.donor, date=target, time=dtime(10, 30), status="REQUESTED",
        )
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("appointments:list"), {"date": target.isoformat()})
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Requested Appointment Dates Awaiting Confirmation", body)
        self.assertIn("Clara", body)
        self.assertIn("10:30 AM", body)

        # Clicking a day via HTMX returns only the selected day panel
        panel = self.client.get(
            reverse("appointments:list"),
            {"date": target.isoformat(), "scope": "month", "panel": "day"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(panel.status_code, 200)
        self.assertIn('id="day-schedule-panel"', panel.content.decode())
        self.assertIn("CLARA DIAZ", panel.content.decode().upper())

    def test_appointments_sidebar_badge_counts_open_and_denies_donor(self):
        target = timezone.localdate() + timedelta(days=1)
        Appointment.objects.create(
            donor=self.donor, date=target, time=dtime(9, 0), status="REQUESTED",
        )
        self.client.force_login(self.staff)
        badge = self.client.get(reverse("appointments:badge"))
        self.assertEqual(badge.status_code, 200)
        self.assertIn("bb-nav-badge", badge.content.decode())
        self.assertIn(">1<", badge.content.decode())

        self.client.force_login(self.donor_user)
        denied = self.client.get(reverse("appointments:badge"))
        self.assertEqual(denied.status_code, 403)
