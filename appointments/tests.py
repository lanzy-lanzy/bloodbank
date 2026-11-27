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


class DonorAppointmentsReadOnlyTests(TestCase):
    def setUp(self):
        from django.test import Client
        self.admin = make_user("donor-cal-admin", role="ADMIN")
        self.staff = make_user("donor-cal-staff", role="STAFF")
        user = make_user("appt-donor", role="DONOR")
        self.donor = make_donor(user=user)
        self.client = Client()
        self.client.login(username="appt-donor", password="Test12345!")

    def test_donor_can_view_own_admin_schedules_in_calendar_without_booking_form(self):
        future = timezone.localdate() + timedelta(days=3)
        Appointment.objects.create(
            donor=self.donor, date=future, time=dtime(9, 0),
            location="Dumingag Blood Center", status="CONFIRMED",
            created_by=self.admin,
        )
        # Non-admin schedule should not appear in donor calendar
        Appointment.objects.create(
            donor=self.donor, date=future, time=dtime(14, 0),
            location="Staff Only Clinic", status="CONFIRMED",
            created_by=self.staff,
        )
        resp = self.client.get(reverse("appointments:my_appointments"), {"date": future.isoformat()})
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Upcoming Schedules Set by Admin", body)
        self.assertIn("Dumingag Blood Center", body)
        self.assertNotIn("Staff Only Clinic", body)
        self.assertNotIn("Request an appointment", body)
        self.assertNotIn("Send request", body)

        # Clicking a date via HTMX returns only the donor day schedule panel
        panel = self.client.get(
            reverse("appointments:my_appointments"),
            {"date": future.isoformat(), "panel": "day"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(panel.status_code, 200)
        self.assertIn('id="my-day-schedule-panel"', panel.content.decode())
        self.assertIn("Dumingag Blood Center", panel.content.decode())

    def test_donor_appointments_badge_counts_upcoming_admin_schedules_and_denies_non_donor(self):
        future = timezone.localdate() + timedelta(days=2)
        past = timezone.localdate() - timedelta(days=2)
        Appointment.objects.create(
            donor=self.donor, date=future, time=dtime(9, 0),
            location="Dumingag Hall", status="CONFIRMED", created_by=self.admin,
        )
        # Past or staff-created should not be counted
        Appointment.objects.create(
            donor=self.donor, date=past, time=dtime(9, 0),
            location="Old Hall", status="COMPLETED", created_by=self.admin,
        )
        Appointment.objects.create(
            donor=self.donor, date=future, time=dtime(13, 0),
            location="Staff Hall", status="CONFIRMED", created_by=self.staff,
        )

        badge = self.client.get(reverse("appointments:my_badge"))
        self.assertEqual(badge.status_code, 200)
        self.assertIn("bb-nav-badge", badge.content.decode())
        self.assertIn(">1<", badge.content.decode())

        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("appointments:my_badge")).status_code, 403)

    def test_donor_cannot_post_self_booking(self):
        future = (timezone.localdate() + timedelta(days=3)).isoformat()
        resp = self.client.post(
            reverse("appointments:my_appointments"),
            {"date": future, "time": "09:30", "location": "Drive", "notes": ""},
        )
        self.assertEqual(resp.status_code, 405)
        self.assertFalse(Appointment.objects.filter(donor=self.donor).exists())

    def test_donor_cannot_open_staff_list(self):
        resp = self.client.get(reverse("appointments:list"))
        self.assertIn(resp.status_code, (403, 302))


class AdminBroadcastScheduleTests(TestCase):
    def setUp(self):
        from audit.models import AuditLog  # noqa: F401
        self.admin = make_user("sched-admin", role="ADMIN")
        self.staff = make_user("sched-staff", role="STAFF")
        self.donor_user = make_user("sched-donor", role="DONOR")
        self.requester = make_user("sched-req", role="REQUESTER")
        self.d1 = make_donor(user=self.donor_user, first_name="Ana", last_name="Cruz")
        self.d2 = make_donor(first_name="Ben", last_name="Reyes")
        self.inactive = make_donor(first_name="Old", last_name="Donor")
        self.inactive.status = "INACTIVE"
        self.inactive.save(update_fields=["status"])

    def test_only_admin_can_access_broadcast_schedule(self):
        url = reverse("appointments:broadcast_schedule")
        future = (timezone.localdate() + timedelta(days=5)).isoformat()
        payload = {"date": future, "time": "08:30", "location": "Dumingag Gymnasium", "notes": "Quarterly drive"}
        for user in (self.staff, self.donor_user, self.requester):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, payload).status_code, 403)
        self.assertEqual(Appointment.objects.count(), 0)

    def test_admin_broadcast_schedules_all_active_donors_and_notifies(self):
        from audit.models import AuditLog

        self.client.force_login(self.admin)
        url = reverse("appointments:broadcast_schedule")
        target_date = timezone.localdate() + timedelta(days=4)

        # Pre-existing conflict for d1 on the same slot should be skipped cleanly.
        Appointment.objects.create(
            donor=self.d1, date=target_date, time=dtime(8, 30),
            location="Existing", status="CONFIRMED",
        )

        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("2 active registered donors", resp.content.decode())

        post_resp = self.client.post(url, {
            "date": target_date.isoformat(),
            "time": "08:30",
            "location": "Dumingag RHU Hall",
            "notes": "Bring valid ID",
            "send_sms": "",
        })
        self.assertEqual(post_resp.status_code, 302)

        # d2 gets a new CONFIRMED appointment; d1 kept its existing one; inactive skipped.
        self.assertTrue(Appointment.objects.filter(
            donor=self.d2, date=target_date, time=dtime(8, 30),
            location="Dumingag RHU Hall", status="CONFIRMED",
        ).exists())
        self.assertFalse(Appointment.objects.filter(donor=self.inactive).exists())

        # In-app notification created for newly scheduled active donor (d2).
        notif = Notification.objects.get(donor=self.d2, channel="in_app")
        self.assertIn("Dumingag RHU Hall", notif.body)
        self.assertIn("Scheduled Blood Donation", notif.subject)

        # Audit log recorded.
        self.assertTrue(AuditLog.objects.filter(action="APPOINTMENT_BROADCAST_SCHEDULED").exists())

    def test_admin_sees_broadcast_button_staff_does_not(self):
        list_url = reverse("appointments:list")
        self.client.force_login(self.admin)
        admin_body = self.client.get(list_url).content.decode()
        self.assertIn("Set Schedule & Notify All", admin_body)

        self.client.force_login(self.staff)
        staff_body = self.client.get(list_url).content.decode()
        self.assertNotIn("Set Schedule & Notify All", staff_body)


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
        self.admin = make_user("cal-admin", role="ADMIN")
        self.staff = make_user("cal-staff", role="STAFF")
        self.donor_user = make_user("cal-donor", role="DONOR")
        self.donor = make_donor(user=self.donor_user, first_name="Clara", last_name="Diaz")
        self.other_donor = make_donor(first_name="Marco", last_name="Lim")

    def test_calendar_shows_only_available_schedules_set_by_admin(self):
        target = timezone.localdate() + timedelta(days=2)
        # Admin-set CONFIRMED schedule -> MUST appear
        Appointment.objects.create(
            donor=self.donor, date=target, time=dtime(10, 30),
            location="Dumingag Civic Center", status="CONFIRMED",
            created_by=self.admin,
        )
        # Staff-created appointment -> must NOT appear on calendar
        Appointment.objects.create(
            donor=self.other_donor, date=target, time=dtime(11, 0),
            location="Staff Clinic", status="CONFIRMED",
            created_by=self.staff,
        )
        # Cancelled admin appointment -> must NOT appear in default available view
        Appointment.objects.create(
            donor=self.other_donor, date=target, time=dtime(14, 0),
            location="Cancelled Hall", status="CANCELLED",
            created_by=self.admin,
        )

        self.client.force_login(self.staff)
        resp = self.client.get(reverse("appointments:list"), {"date": target.isoformat()})
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Available Admin-Set Schedules", body)
        self.assertIn("Dumingag Civic Center", body)
        self.assertIn("10:30 AM", body)
        self.assertNotIn("Staff Clinic", body)
        self.assertNotIn("Cancelled Hall", body)

        # Clicking a day via HTMX returns only the selected day panel with admin-set schedule
        panel = self.client.get(
            reverse("appointments:list"),
            {"date": target.isoformat(), "scope": "month", "panel": "day"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(panel.status_code, 200)
        panel_body = panel.content.decode()
        self.assertIn('id="day-schedule-panel"', panel_body)
        self.assertIn("CLARA DIAZ", panel_body.upper())
        self.assertNotIn("MARCO LIM", panel_body.upper())

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
