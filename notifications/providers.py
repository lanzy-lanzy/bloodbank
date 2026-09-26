"""Notification provider interfaces + implementations.

The SMS channel is selected by settings.SMS_PROVIDER (env-driven):
  "mock"      -> MockSMSProvider — clearly-labelled DEVELOPMENT ONLY, logs only.
  "semaphore" -> SemaphoreSMSProvider — live gateway; needs SMS_API_KEY (+
                 optional SMS_SENDER_NAME). Any other value fails safe.
The app never claims a real SMS send it did not perform.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from core.validators import normalize_ph_mobile as _normalize_ph_mobile

logger = logging.getLogger("bloodbank.notifications")


def _recipient_mobile(notification):
    if notification.donor:
        return notification.donor.contact_number
    if notification.user:
        return notification.user.phone
    return ""


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
        recipient = _recipient_mobile(notification)
        logger.warning(
            "[MOCK SMS — DEVELOPMENT ONLY, nothing actually sent] to=%s sender=%s body=%s",
            recipient, settings.SMS_SENDER_NAME, notification.body,
        )
        if not recipient:
            return False, "No mobile number on record for recipient"
        return True, ""


class SemaphoreSMSProvider(NotificationProvider):
    """Live SMS via Semaphore API v4 (https://semaphore.co/docs).

    Selected when SMS_PROVIDER=semaphore. All credentials come from the
    environment: SMS_API_KEY (required), SMS_SENDER_NAME (optional registered
    sender ID). Without an API key every send fails safe — nothing is sent
    and the reason is recorded on the notification."""

    channel = "sms"
    label = "Semaphore SMS"
    FAILED_STATUSES = {"Failed", "Refunded"}

    @property
    def api_url(self):
        # Overridable via SMS_API_URL if Semaphore's hosted base ever moves.
        return getattr(settings, "SMS_API_URL", "https://semaphore.co/api/v4/messages")

    @property
    def timeout_seconds(self):
        return getattr(settings, "SMS_TIMEOUT_SECONDS", 10)

    def send(self, notification):
        api_key = settings.SMS_API_KEY
        if not api_key:
            return False, ("Semaphore SMS is not configured: set SMS_API_KEY in the "
                           "environment. No message was sent.")
        number = _normalize_ph_mobile(_recipient_mobile(notification))
        if not number:
            return False, "No valid Philippine mobile number on record for recipient"
        fields = {"apikey": api_key, "number": number, "message": notification.body or ""}
        if settings.SMS_SENDER_NAME:
            fields["sendername"] = settings.SMS_SENDER_NAME
        status_code, response_text = self._post(fields)
        excerpt = response_text[:300]
        if not 200 <= status_code < 300:
            logger.error("Semaphore SMS rejected (HTTP %s): %s", status_code, excerpt)
            return False, f"Semaphore HTTP {status_code}: {excerpt}"
        try:
            payload = json.loads(response_text)
        except ValueError:
            return False, f"Semaphore returned an unparseable response: {excerpt}"
        reports = payload if isinstance(payload, list) else [payload]
        for item in reports:
            status = str(item.get("status", "")) if isinstance(item, dict) else ""
            if status in self.FAILED_STATUSES:
                return False, f"Semaphore reported delivery status '{status}'"
        logger.info("Semaphore SMS queued for %s", number)
        return True, ""

    def _post(self, fields: dict) -> tuple[int, str]:
        data = urllib.parse.urlencode(fields).encode("utf-8")
        request = urllib.request.Request(self.api_url, data=data, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            return 0, str(reason)


def get_provider(channel: str) -> NotificationProvider:
    if channel == "sms":
        if settings.SMS_PROVIDER == "semaphore":
            return SemaphoreSMSProvider()
        return MockSMSProvider()
    return {
        "in_app": InAppProvider(),
        "email": DjangoEmailProvider(),
    }[channel]
