"""Donor domain models: donor records, configurable screening questionnaire,
screening sessions and responses."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def next_donor_code():
    last = Donor.all_objects.order_by("-donor_code").values_list("donor_code", flat=True).first()
    seq = int(last.split("-")[-1]) + 1 if last else 1
    return f"DON-{seq:06d}"


class DonorQuerySet(models.QuerySet):
    def active_only(self):
        return self.filter(is_deleted=False)


class DonorManager(models.Manager):
    def get_queryset(self):
        return DonorQuerySet(self.model, using=self._db).filter(is_deleted=False)


class Donor(models.Model):
    """A registered blood donor. Soft-deleted: history must remain available."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        TEMP_DEFERRED = "TEMP_DEFERRED", "Temporarily deferred"
        PERM_DEFERRED = "PERM_DEFERRED", "Permanently deferred"
        INACTIVE = "INACTIVE", "Inactive"
        BLACKLISTED = "BLACKLISTED", "Blacklisted / restricted"

    class Sex(models.TextChoices):
        MALE = "MALE", "Male"
        FEMALE = "FEMALE", "Female"
        OTHER = "OTHER", "Other"
        UNDISCLOSED = "UNDISCLOSED", "Prefer not to say"

    donor_code = models.CharField(max_length=16, unique=True, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="donor_profile",
    )
    first_name = models.CharField(max_length=60)
    middle_name = models.CharField(max_length=60, blank=True)
    last_name = models.CharField(max_length=60)
    suffix = models.CharField(max_length=10, blank=True)
    date_of_birth = models.DateField()
    sex = models.CharField(max_length=12, choices=Sex.choices)
    contact_number = models.CharField(max_length=30)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    municipality = models.CharField(max_length=120, blank=True, db_index=True)
    province = models.CharField(max_length=120, blank=True)
    emergency_contact_name = models.CharField(max_length=120, blank=True)
    emergency_contact_phone = models.CharField(max_length=30, blank=True)

    blood_type = models.ForeignKey(
        "inventory.BloodType", on_delete=models.SET_NULL, null=True, blank=True, related_name="donors"
    )
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    deferral_reason = models.CharField(max_length=255, blank=True)
    deferred_until = models.DateField(null=True, blank=True)

    # Cached points balance — the authoritative history is the rewards ledger.
    points_balance = models.IntegerField(default=0)

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = DonorManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["last_name", "first_name"]
        indexes = [models.Index(fields=["status", "blood_type"])]

    def __str__(self):
        return f"{self.donor_code} — {self.full_name}"

    def save(self, *args, **kwargs):
        if not self.donor_code:
            self.donor_code = next_donor_code()
        if self.date_of_birth and self.date_of_birth > timezone.now().date():
            raise ValidationError("Date of birth cannot be in the future.")
        super().save(*args, **kwargs)

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at"])

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name, self.suffix]
        return " ".join(p for p in parts if p)

    @property
    def age(self):
        today = timezone.now().date()
        return (today - self.date_of_birth).days // 365

    @property
    def last_donation(self):
        return self.donations.filter(status__in=["COLLECTED", "RELEASED"]).order_by("-donation_date").first()

    @property
    def contactable(self):
        return bool(self.contact_number or self.email)


class ScreeningQuestion(models.Model):
    """Configurable medical-questionnaire item. Questions are defined by the
    institution, not hard-coded in the application."""

    class AnswerType(models.TextChoices):
        YESNO = "YESNO", "Yes / No"
        TEXT = "TEXT", "Free text"
        NUMBER = "NUMBER", "Number"
        CHOICE = "CHOICE", "Choices (comma-separated)"

    text = models.TextField()
    category = models.CharField(max_length=60, default="General")
    answer_type = models.CharField(max_length=6, choices=AnswerType.choices, default=AnswerType.YESNO)
    choices_text = models.CharField(max_length=255, blank=True, help_text="Comma-separated options for CHOICE type")
    required = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["category", "order", "id"]

    def __str__(self):
        return f"[{self.category}] {self.text[:60]}"

    @property
    def choices(self):
        return [c.strip() for c in self.choices_text.split(",") if c.strip()]


class DonorScreening(models.Model):
    """One screening session for a donor (optionally linked to a donation)."""

    class Result(models.TextChoices):
        CLEARED = "CLEARED", "Cleared"
        DEFERRED = "DEFERRED", "Deferred"
        REJECTED = "REJECTED", "Rejected"
        REQUIRES_REVIEW = "REQUIRES_REVIEW", "Requires review"

    donor = models.ForeignKey(Donor, on_delete=models.PROTECT, related_name="screenings")
    donation = models.OneToOneField(
        "donations.Donation", on_delete=models.SET_NULL, null=True, blank=True, related_name="screening"
    )
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                     related_name="screenings_performed")
    performed_at = models.DateTimeField(default=timezone.now)

    # Basic screening / vitals
    identity_verified = models.BooleanField(default=False)
    consent_given = models.BooleanField(default=False)
    weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    temperature_c = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    bp_systolic = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic = models.PositiveSmallIntegerField(null=True, blank=True)
    pulse_bpm = models.PositiveSmallIntegerField(null=True, blank=True)
    hemoglobin_gdl = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)

    result = models.CharField(max_length=16, choices=Result.choices, default=Result.REQUIRES_REVIEW)
    deferral_days = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Staff-decided temporary deferral length (clinical judgement — not computed by the system).",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-performed_at"]

    def __str__(self):
        return f"Screening {self.donor.donor_code} {self.performed_at:%Y-%m-%d} → {self.result}"

    def clean(self):
        if self.result == self.Result.CLEARED and not (self.identity_verified and self.consent_given):
            raise ValidationError("Identity verification and consent are required before a donor can be CLEARED.")


class ScreeningResponse(models.Model):
    screening = models.ForeignKey(DonorScreening, on_delete=models.CASCADE, related_name="responses")
    question = models.ForeignKey(ScreeningQuestion, on_delete=models.PROTECT, related_name="responses")
    answer_text = models.TextField(blank=True)

    class Meta:
        unique_together = ("screening", "question")
        ordering = ["question__category", "question__order"]

    def __str__(self):
        return f"{self.question_id}: {self.answer_text[:40]}"
