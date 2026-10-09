"""Appointment scheduling models."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Appointment(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested"
        CONFIRMED = "CONFIRMED", "Confirmed"
        CHECKED_IN = "CHECKED_IN", "Checked in"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"
        NO_SHOW = "NO_SHOW", "No show"

    OPEN_STATUSES = ["REQUESTED", "CONFIRMED", "CHECKED_IN"]

    donor = models.ForeignKey("donors.Donor", on_delete=models.CASCADE, related_name="appointments")
    date = models.DateField(db_index=True)
    time = models.TimeField()
    location = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="appointments_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "time"]
        constraints = [
            # A donor cannot hold two open appointments in the same slot.
            # MySQL/MariaDB cannot build a partial index, so 0003_mysql_open_slot_unique
            # recreates this guard there with a generated column + UNIQUE index.
            models.UniqueConstraint(
                fields=["donor", "date", "time"],
                condition=~models.Q(status__in=["CANCELLED", "NO_SHOW", "COMPLETED"]),
                name="unique_open_appointment_slot",
            ),
        ]

    def __str__(self):
        return f"{self.donor.donor_code} {self.date} {self.time:%H:%M} ({self.status})"

    def clean(self):
        if self.date and self.date < timezone.localdate() and self.status in self.OPEN_STATUSES:
            raise ValidationError("Open appointments cannot be scheduled in the past.")

    @classmethod
    def has_conflict(cls, donor, date, time, exclude_pk=None):
        qs = cls.objects.filter(donor=donor, date=date, time=time, status__in=cls.OPEN_STATUSES)
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)
        return qs.exists()

    @classmethod
    def day_capacity_used(cls, date):
        return cls.objects.filter(date=date, status__in=cls.OPEN_STATUSES + ["COMPLETED"]).count()
