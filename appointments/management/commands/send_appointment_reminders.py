"""Remind donors of upcoming appointments via the 'appointment_reminder' template.

Deduplicates: skips a donor's appointment if a reminder was already sent for it
within the last 20 hours.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from appointments.models import Appointment
from notifications.models import Notification, NotificationTemplate
from notifications.services import NotificationService


class Command(BaseCommand):
    help = "Send appointment reminders to donors."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=1,
                            help="Remind appointments this many day(s) ahead (default: 1).")

    def handle(self, *args, **options):
        target = timezone.localdate() + timedelta(days=options["days"])
        appts = Appointment.objects.filter(
            date=target, status__in=[Appointment.Status.REQUESTED, Appointment.Status.CONFIRMED],
        ).select_related("donor")

        if not appts.exists():
            self.stdout.write(f"No open appointments on {target:%Y-%m-%d}. Nothing to send.")
            return

        template = NotificationTemplate.objects.filter(code="appointment_reminder", is_active=True).first()
        if template is None:
            self.stdout.write(self.style.ERROR(
                "No active 'appointment_reminder' notification template. "
                "Create one under Notifications -> Templates (or run seed_demo)."))
            return

        cutoff = timezone.now() - timedelta(hours=20)
        sent = failed = skipped = 0
        for appt in appts:
            if Notification.objects.filter(
                donor=appt.donor, template=template, sent_at__gte=cutoff,
            ).exists():
                skipped += 1
                continue
            try:
                NotificationService.send_to_donor(
                    appt.donor, "appointment_reminder",
                    {
                        "donor_name": appt.donor.full_name,
                        "appointment_date": f"{appt.date:%A, %B %d, %Y}",
                        "appointment_time": f"{appt.time:%H:%M}",
                        "location": appt.location or "the blood center",
                    },
                )
                sent += 1
            except Exception as exc:  # noqa: BLE001 — one failure must not stop the batch
                failed += 1
                self.stdout.write(self.style.ERROR(f"Reminder failed for {appt.donor.donor_code}: {exc}"))

        self.stdout.write(self.style.SUCCESS(
            f"Appointment reminders for {target:%Y-%m-%d}: {sent} sent, {skipped} already reminded, {failed} failed."))
