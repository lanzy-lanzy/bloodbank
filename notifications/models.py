"""Notification domain: templates, notification records, delivery tracking."""
import re
import uuid

from django.conf import settings
from django.db import models


VARIABLE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

KNOWN_VARIABLES = [
    "donor_name", "blood_type", "request_id", "organization",
    "appointment_date", "appointment_time", "location", "quantity",
    "component", "required_by", "urgency", "points", "reward_name",
    "tier_name", "response_url", "donation_code", "bag_code",
    "full_name", "role", "rejection_reason",
]


class NotificationTemplate(models.Model):
    """Admin-configurable message template. Variables use {{ name }} syntax and
    are validated against KNOWN_VARIABLES before saving."""

    name = models.CharField(max_length=120)
    code = models.SlugField(max_length=60, unique=True, help_text="Referenced by services, e.g. emergency_alert")
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    channels = models.CharField(
        max_length=40, default="in_app",
        help_text="Comma-separated: in_app, email, sms",
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})"

    @property
    def channel_list(self):
        return [c.strip() for c in self.channels.split(",") if c.strip()]

    def variables_used(self):
        return sorted(set(VARIABLE_RE.findall(self.subject + " " + self.body)))

    def render(self, context: dict) -> tuple[str, str]:
        def sub(match):
            key = match.group(1)
            return str(context.get(key, match.group(0)))
        return VARIABLE_RE.sub(sub, self.subject), VARIABLE_RE.sub(sub, self.body)

    def clean(self):
        from django.core.exceptions import ValidationError
        unknown = [v for v in self.variables_used() if v not in KNOWN_VARIABLES]
        if unknown:
            raise ValidationError(
                f"Unknown template variables: {', '.join(unknown)}. "
                f"Allowed: {', '.join(KNOWN_VARIABLES)}"
            )


class Notification(models.Model):
    class Channel(models.TextChoices):
        IN_APP = "in_app", "In-app"
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"

    class DeliveryStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SENT = "SENT", "Sent"
        DELIVERED = "DELIVERED", "Delivered"
        FAILED = "FAILED", "Failed"

    class DonorResponse(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available to donate"
        UNAVAILABLE = "UNAVAILABLE", "Unavailable"
        MAYBE = "MAYBE", "Maybe"
        CONTACT_ME = "CONTACT_ME", "Contact me"

    donor = models.ForeignKey("donors.Donor", on_delete=models.CASCADE, null=True, blank=True,
                              related_name="notifications")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
                             related_name="notifications")
    blood_request = models.ForeignKey("requests.BloodRequest", on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="notifications")
    template = models.ForeignKey(NotificationTemplate, on_delete=models.SET_NULL, null=True, blank=True)
    channel = models.CharField(max_length=6, choices=Channel.choices, default=Channel.IN_APP)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    delivery_status = models.CharField(max_length=10, choices=DeliveryStatus.choices,
                                       default=DeliveryStatus.PENDING, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    retry_count = models.PositiveIntegerField(default=0)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    donor_response = models.CharField(max_length=12, choices=DonorResponse.choices, null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    response_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["donor", "is_read"])]

    def __str__(self):
        target = self.donor.donor_code if self.donor else (self.user.username if self.user else "?")
        return f"[{self.channel}] {target} — {self.delivery_status}"

    @property
    def is_emergency_response_request(self):
        return self.donor_response is not None or self.template_id is not None
