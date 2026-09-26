"""NotificationService — rendering, dispatch and emergency donor notification."""
import logging

from django.db import transaction
from django.utils import timezone

from notifications.models import Notification, NotificationTemplate
from notifications.providers import get_provider

logger = logging.getLogger("bloodbank.notifications")


class NotificationError(Exception):
    pass


class NotificationService:
    @staticmethod
    def send_to_donor(donor, template_code, context, *, blood_request=None,
                      channels=None, actor=None, request=None):
        """Render a template and dispatch on each configured channel.
        One Notification row per channel; delivery outcomes recorded per row."""
        from audit import services as audit

        template = NotificationTemplate.objects.filter(code=template_code, is_active=True).first()
        if template is None:
            raise NotificationError(f"No active notification template with code '{template_code}'.")
        subject, body = template.render(context)
        channels = channels or template.channel_list or ["in_app"]

        created = []
        for channel in channels:
            notification = Notification.objects.create(
                donor=donor, blood_request=blood_request, template=template,
                channel=channel, subject=subject, body=body,
                delivery_status=Notification.DeliveryStatus.PENDING,
            )
            NotificationService.dispatch(notification)
            created.append(notification)

        audit.log(request, user=actor, action="NOTIFICATION_SENT", module="notifications",
                  obj=created[0] if created else None, object_type="Donor", object_id=str(donor.pk),
                  description=f"Notification '{template_code}' → donor {donor.donor_code} via {', '.join(channels)}")
        return created

    @staticmethod
    def send_to_user(user, template_code, context, *, channels=None, actor=None, request=None):
        template = NotificationTemplate.objects.filter(code=template_code, is_active=True).first()
        if template is None:
            raise NotificationError(f"No active notification template with code '{template_code}'.")
        subject, body = template.render(context)
        channels = channels or template.channel_list or ["in_app"]
        created = []
        for channel in channels:
            notification = Notification.objects.create(
                user=user, template=template, channel=channel, subject=subject, body=body,
            )
            NotificationService.dispatch(notification)
            created.append(notification)
        return created

    @staticmethod
    def dispatch(notification) -> Notification:
        """Hand a notification to its channel provider and record the outcome."""
        provider = get_provider(notification.channel)
        success, error = provider.send(notification)
        if success:
            notification.delivery_status = Notification.DeliveryStatus.SENT
            notification.sent_at = timezone.now()
            notification.error = ""
        else:
            notification.delivery_status = Notification.DeliveryStatus.FAILED
            notification.error = error
            notification.retry_count += 1
            logger.error("Notification %s (%s) FAILED: %s", notification.pk, notification.channel, error)
        notification.save(update_fields=["delivery_status", "sent_at", "error", "retry_count"])
        return notification

    @staticmethod
    def retry_failed(notification):
        if notification.delivery_status != Notification.DeliveryStatus.FAILED:
            raise NotificationError("Only FAILED notifications can be retried.")
        notification.retry_count += 1
        notification.save(update_fields=["retry_count"])
        return NotificationService.dispatch(notification)

    @staticmethod
    def emergency_donor_notification(blood_request, donors, *, actor=None, request=None):
        """Notify a candidate donor pool about an emergency request.

        Being notified is NOT a medical eligibility determination — every
        responding donor must still go through the standard screening process.
        """
        from audit import services as audit

        item = blood_request.items.first()
        context = {
            "donor_name": "",  # personalised per donor below
            "blood_type": str(item.blood_type) if item else "",
            "component": str(item.component) if item else "",
            "quantity": str(item.outstanding) if item else "",
            "request_id": blood_request.request_code,
            "organization": blood_request.organization.name,
            "urgency": blood_request.get_urgency_display(),
            "required_by": timezone.localtime(blood_request.required_by).strftime("%b %d, %Y %H:%M")
            if blood_request.required_by else "",
        }
        sent, failed = [], []
        for donor in donors:
            donor_ctx = dict(context, donor_name=donor.full_name)
            try:
                notifications = NotificationService.send_to_donor(
                    donor, "emergency_alert", donor_ctx,
                    blood_request=blood_request, actor=actor, request=request,
                )
                sent.append((donor, notifications))
            except NotificationError as exc:
                logger.warning("Emergency notification failed for donor %s: %s", donor.donor_code, exc)
                failed.append((donor, str(exc)))
        audit.log(request, user=actor, action="EMERGENCY_NOTIFICATION_SENT", module="notifications",
                  obj=blood_request,
                  description=f"Emergency alert for {blood_request.request_code}: "
                              f"{len(sent)} notified, {len(failed)} failed")
        return sent, failed

    @staticmethod
    def record_response(notification, response_value):
        """Donor responds AVAILABLE / UNAVAILABLE / MAYBE / CONTACT_ME.
        Never treated as medical eligibility confirmation."""
        from audit import services as audit

        with transaction.atomic():
            notification.donor_response = response_value
            notification.responded_at = timezone.now()
            notification.save(update_fields=["donor_response", "responded_at"])
            audit.log(None, action="DONOR_RESPONSE_RECORDED", module="notifications", obj=notification,
                      description=f"Donor {notification.donor.donor_code} responded '{response_value}' "
                                  f"to notification for request "
                                  f"{notification.blood_request.request_code if notification.blood_request else '—'}. "
                                  "Response is not a medical eligibility confirmation.")
        return notification
