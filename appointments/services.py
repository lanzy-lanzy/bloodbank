import logging

from notifications.services import NotificationError, NotificationService

logger = logging.getLogger("bloodbank.appointments")


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
