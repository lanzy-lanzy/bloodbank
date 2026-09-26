"""Donation + appointment domain models."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def next_donation_code():
    year = timezone.now().year
    last = (
        Donation.objects.filter(donation_code__startswith=f"DN-{year}-")
        .order_by("-donation_code").values_list("donation_code", flat=True).first()
    )
    seq = int(last.split("-")[-1]) + 1 if last else 1
    return f"DN-{year}-{seq:06d}"


class Donation(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Scheduled"
        REGISTERED = "REGISTERED", "Registered"
        SCREENING = "SCREENING", "Screening"
        APPROVED = "APPROVED", "Approved"
        COLLECTED = "COLLECTED", "Collected"
        TESTING = "TESTING", "Testing"
        RELEASED = "RELEASED", "Released"
        DEFERRED = "DEFERRED", "Deferred"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"

    class DonationType(models.TextChoices):
        VOLUNTARY = "VOLUNTARY", "Voluntary"
        REPLACEMENT = "REPLACEMENT", "Replacement"
        DIRECTED = "DIRECTED", "Directed"
        AUTOLOGOUS = "AUTOLOGOUS", "Autologous"

    TRANSITIONS = {
        "SCHEDULED": ["REGISTERED", "CANCELLED", "DEFERRED"],
        "REGISTERED": ["SCREENING", "CANCELLED", "DEFERRED"],
        "SCREENING": ["APPROVED", "DEFERRED", "REJECTED", "CANCELLED"],
        "APPROVED": ["COLLECTED", "CANCELLED", "DEFERRED"],
        "COLLECTED": ["TESTING", "RELEASED"],
        "TESTING": ["RELEASED"],
        "DEFERRED": [], "REJECTED": [], "CANCELLED": [], "RELEASED": [],
    }

    donation_code = models.CharField(max_length=20, unique=True, editable=False)
    donor = models.ForeignKey("donors.Donor", on_delete=models.PROTECT, related_name="donations")
    appointment = models.OneToOneField(
        "appointments.Appointment", on_delete=models.SET_NULL, null=True, blank=True, related_name="donation"
    )
    donation_date = models.DateField(default=timezone.localdate, db_index=True)
    donation_time = models.TimeField(null=True, blank=True)
    location = models.CharField(max_length=120, blank=True)
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="donations_recorded"
    )
    donation_type = models.CharField(max_length=12, choices=DonationType.choices, default=DonationType.VOLUNTARY)
    volume_ml = models.PositiveIntegerField(null=True, blank=True)
    blood_type = models.ForeignKey(
        "inventory.BloodType", on_delete=models.SET_NULL, null=True, blank=True, related_name="donations"
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REGISTERED, db_index=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-donation_date", "-created_at"]
        indexes = [models.Index(fields=["donor", "status"])]

    def __str__(self):
        return f"{self.donation_code} — {self.donor.full_name}"

    def save(self, *args, **kwargs):
        if not self.donation_code:
            self.donation_code = next_donation_code()
        super().save(*args, **kwargs)

    def can_transition_to(self, new_status):
        return new_status in self.TRANSITIONS.get(self.status, [])

    @property
    def is_completed(self):
        return self.status in ("COLLECTED", "TESTING", "RELEASED")
