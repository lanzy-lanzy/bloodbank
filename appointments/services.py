"""Appointment services: notifications and admin-only broadcast scheduling."""
import logging
from dataclasses import dataclass

from django.db import transaction

from audit import services as audit
from notifications.models import Notification
from notifications.services import NotificationError, NotificationService

logger = logging.getLogger("bloodbank.appointments")


@dataclass(frozen=True)
class BroadcastScheduleResult:
    scheduled_count: int
    skipped_count: int
    notified_count: int


def notify_confirmation(appointment, *, previous_status=None, request=None):
    if appointment.status != appointment.Status.CONFIRMED or previous_status == appointment.Status.CONFIRMED:
        return
    try:
        NotificationService.send_to_donor(
            appointment.donor, "appointment_confirmed_sms",
            {
                "appointment_date": appointment.date.strftime("%b %d, %Y"),
                "appointment_time": appointment.time.strftime("%I:%M %p").lstrip("0"),
                "location": f" at {appointment.location}" if appointment.location else "",
            },
            channels=["sms"], after_commit=True, actor=request.user if request else None, request=request,
        )
    except NotificationError:
        logger.exception("Confirmation SMS template unavailable for appointment %s", appointment.pk)


def schedule_all_donors_and_notify(
    *,
    date,
    time,
    location: str,
    notes: str = "",
    actor,
    request=None,
    send_sms: bool = True,
) -> BroadcastScheduleResult:
    """Create a confirmed appointment for every active registered donor and notify them.

    Skips donors who already hold an open appointment in the exact same slot.
    Always creates an in-app notification for every scheduled donor, and
    optionally dispatches an SMS notification after commit.
    """
    from appointments.models import Appointment
    from donors.models import Donor

    donors = list(Donor.objects.filter(status=Donor.Status.ACTIVE).order_by("pk"))
    date_label = date.strftime("%B %d, %Y")
    time_label = time.strftime("%I:%M %p").lstrip("0")
    loc_text = location.strip()
    notes_text = (notes or "").strip()

    subject = f"Scheduled Blood Donation — {date_label}"
    body_lines = [
        f"A blood donation schedule has been set by the blood bank administrator for {date_label} at {time_label}.",
        f"Location: {loc_text}",
    ]
    if notes_text:
        body_lines.append(f"Notes: {notes_text}")
    body_lines.append(
        "Please bring a valid ID and follow standard pre-donation preparation. "
        "Final eligibility is always confirmed by staff at screening."
    )
    body = "\n".join(body_lines)

    scheduled = 0
    skipped = 0
    notified = 0

    with transaction.atomic():
        for donor in donors:
            if Appointment.has_conflict(donor, date, time):
                skipped += 1
                continue
            appointment = Appointment.objects.create(
                donor=donor,
                date=date,
                time=time,
                location=loc_text,
                notes=notes_text,
                status=Appointment.Status.CONFIRMED,
                created_by=actor,
            )
            scheduled += 1

            notif = Notification.objects.create(
                donor=donor,
                channel=Notification.Channel.IN_APP,
                subject=subject,
                body=body,
                delivery_status=Notification.DeliveryStatus.PENDING,
            )
            NotificationService.dispatch(notif)
            notified += 1

            if send_sms and donor.contact_number:
                notify_confirmation(appointment, request=request)

        audit.log(
            request,
            user=actor,
            action="APPOINTMENT_BROADCAST_SCHEDULED",
            module="appointments",
            object_type="Appointment",
            description=(
                f"Broadcast schedule set for {date} {time:%H:%M} at {loc_text}: "
                f"{scheduled} donor(s) scheduled and notified, {skipped} skipped (slot conflict)"
            ),
        )

    return BroadcastScheduleResult(
        scheduled_count=scheduled,
        skipped_count=skipped,
        notified_count=notified,
    )
