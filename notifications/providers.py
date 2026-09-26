"""Notification provider interfaces + implementations.

IMPORTANT: No live SMS provider is configured or claimed operational. The SMS
implementation below is a clearly-labelled MOCK for development. Real
providers (Semaphore, Twilio, …) plug in by implementing SMSProvider and
selecting them via the SMS_PROVIDER setting + credentials.
"""
import logging

from django.conf import settings

logger = logging.getLogger("bloodbank.notifications")


class NotificationProvider:
    """Base interface: send(notification) -> (success: bool, error: str)."""

    channel = ""
    label = ""

    def send(self, notification) -> tuple[bool, str]:
        raise NotImplementedError


class InAppProvider(NotificationProvider):
    channel = "in_app"
    label = "In-app inbox"

    def send(self, notification):
        # In-app notifications exist the moment they are stored.
        return True, ""


class DjangoEmailProvider(NotificationProvider):
    """Email via Django's configured EMAIL_BACKEND. With the default console
    backend this is DEVELOPMENT ONLY — configure SMTP env vars for real mail."""

    channel = "email"
    label = "Django email backend"

    def send(self, notification):
        from django.core.mail import send_mail

        recipient = None
        if notification.donor and notification.donor.email:
            recipient = notification.donor.email
        elif notification.user and notification.user.email:
            recipient = notification.user.email
        if not recipient:
            return False, "No email address on record for recipient"
        try:
            send_mail(
                subject=notification.subject or f"[{settings.SMS_SENDER_NAME}] Notification",
                message=notification.body,
                from_email=None,
                recipient_list=[recipient],
                fail_silently=False,
            )
            return True, ""
        except Exception as exc:  # noqa: BLE001 — delivery errors are recorded, not raised
            logger.exception("Email delivery failed")
            return False, str(exc)


class MockSMSProvider(NotificationProvider):
    """MOCK — DEVELOPMENT ONLY — NOT CONFIGURED FOR REAL DELIVERY.

    Logs the SMS to the application log and reports success so workflows can be
    exercised end-to-end without a live gateway. Never present this as an
    operational SMS integration.
    """

    channel = "sms"
    label = "Mock SMS (DEVELOPMENT ONLY)"

    def send(self, notification):
        if settings.SMS_PROVIDER != "mock":
            # A real provider was requested but is not implemented/configured.
            return False, (
                f"SMS provider '{settings.SMS_PROVIDER}' is not implemented or credentials "
                "are missing. No message was sent."
            )
        recipient = None
        if notification.donor:
            recipient = notification.donor.contact_number
        elif notification.user:
            recipient = notification.user.phone
        logger.warning(
            "[MOCK SMS — DEVELOPMENT ONLY, nothing actually sent] to=%s sender=%s body=%s",
            recipient, settings.SMS_SENDER_NAME, notification.body,
        )
        if not recipient:
            return False, "No mobile number on record for recipient"
        return True, ""


def get_provider(channel: str) -> NotificationProvider:
    return {
        "in_app": InAppProvider(),
        "email": DjangoEmailProvider(),
        "sms": MockSMSProvider(),
    }[channel]
