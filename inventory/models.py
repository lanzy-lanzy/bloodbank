"""Inventory domain models: blood types, components, bags, tests, ledger, compatibility.

SAFETY NOTE: the state machine below is a *software mechanism*. Which
transitions are clinically/operationally authorised, and which tests must pass
before release, are institutional configuration (TestType.is_required,
CompatibilityRule rows, release permission). Default seed values are labelled
DEMO and require institutional validation before production use.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


def next_bag_code():
    """BB-<year>-<sequence>, e.g. BB-2026-000001."""
    year = timezone.now().year
    last = (
        BloodBag.objects.filter(bag_code__startswith=f"BB-{year}-")
        .order_by("-bag_code")
        .values_list("bag_code", flat=True)
        .first()
    )
    seq = int(last.split("-")[-1]) + 1 if last else 1
    return f"BB-{year}-{seq:06d}"


class BloodType(models.Model):
    """Configurable ABO/Rh blood types (not hard-coded)."""

    ABO_CHOICES = [("A", "A"), ("B", "B"), ("AB", "AB"), ("O", "O")]
    RH_CHOICES = [("POS", "+"), ("NEG", "−")]

    abo = models.CharField(max_length=2, choices=ABO_CHOICES)
    rh = models.CharField(max_length=3, choices=RH_CHOICES)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("abo", "rh")
        ordering = ["abo", "rh"]

    @property
    def code(self):
        return f"{self.abo}{'+' if self.rh == 'POS' else '-'}"

    def __str__(self):
        return self.code


class BloodComponent(models.Model):
    """Configurable blood components with per-component shelf life (days)."""

    name = models.CharField(max_length=80, unique=True)
    code = models.SlugField(max_length=20, unique=True)
    default_shelf_life_days = models.PositiveIntegerField(
        help_text="Used to compute expiration from collection time. Institutional value — validate before production."
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class TestType(models.Model):
    """Configurable safety-screening test definitions (e.g. grouping, infectious disease)."""

    CATEGORY_CHOICES = [
        ("GROUPING", "Blood grouping"),
        ("RH", "Rh typing"),
        ("INFECTIOUS", "Infectious-disease screening"),
        ("OTHER", "Other institutional test"),
    ]

    name = models.CharField(max_length=120, unique=True)
    category = models.CharField(max_length=12, choices=CATEGORY_CHOICES, default="OTHER")
    is_required = models.BooleanField(
        default=False,
        help_text="Required tests must be completed and acceptable before a bag can be released.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "name"]

    def __str__(self):
        return self.name


class BloodBag(models.Model):
    """A single physical blood bag with strict lifecycle state."""

    class Status(models.TextChoices):
        QUARANTINED = "QUARANTINED", "Quarantined"
        TESTING = "TESTING", "Testing"
        AVAILABLE = "AVAILABLE", "Available"
        RESERVED = "RESERVED", "Reserved"
        ISSUED = "ISSUED", "Issued"
        TRANSFUSED = "TRANSFUSED", "Transfused"
        EXPIRED = "EXPIRED", "Expired"
        DISCARDED = "DISCARDED", "Discarded"
        RETURNED = "RETURNED", "Returned"
        REJECTED = "REJECTED", "Rejected"

    class ScreeningStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        COMPLETE = "COMPLETE", "Complete"

    # Allowed transitions (mechanism). Guards (tests verified, permissions)
    # are enforced by InventoryService.transition().
    TRANSITIONS = {
        "QUARANTINED": ["TESTING", "REJECTED", "DISCARDED"],
        "TESTING": ["AVAILABLE", "QUARANTINED", "REJECTED", "DISCARDED"],
        "AVAILABLE": ["RESERVED", "ISSUED", "EXPIRED", "DISCARDED"],
        "RESERVED": ["AVAILABLE", "ISSUED", "EXPIRED", "DISCARDED"],
        "ISSUED": ["TRANSFUSED", "RETURNED"],
        "RETURNED": ["AVAILABLE", "EXPIRED", "DISCARDED"],
        "EXPIRED": ["DISCARDED"],
        "REJECTED": ["DISCARDED"],
        "DISCARDED": [],
        "TRANSFUSED": [],
    }

    bag_code = models.CharField(max_length=20, unique=True, editable=False)
    donation = models.ForeignKey(
        "donations.Donation", on_delete=models.PROTECT, null=True, blank=True,
        related_name="blood_bags",
        help_text="Null only for externally sourced bags registered manually (audited).",
    )
    donor = models.ForeignKey(
        "donors.Donor", on_delete=models.PROTECT, null=True, blank=True, related_name="blood_bags"
    )
    blood_type = models.ForeignKey(BloodType, on_delete=models.PROTECT, related_name="bags")
    component = models.ForeignKey(BloodComponent, on_delete=models.PROTECT, related_name="bags")
    collected_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    volume_ml = models.PositiveIntegerField(default=450)
    location = models.CharField(max_length=120, blank=True, help_text="Storage facility / fridge")
    storage_position = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUARANTINED, db_index=True)
    screening_status = models.CharField(
        max_length=12, choices=ScreeningStatus.choices, default=ScreeningStatus.PENDING
    )
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="released_bags",
    )
    released_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-collected_at"]
        indexes = [
            models.Index(fields=["status", "expires_at"]),
            models.Index(fields=["blood_type", "component", "status"]),
        ]

    def __str__(self):
        return self.bag_code

    def save(self, *args, **kwargs):
        if not self.bag_code:
            self.bag_code = next_bag_code()
        super().save(*args, **kwargs)

    # --- state helpers ---------------------------------------------------------
    def can_transition_to(self, new_status) -> bool:
        return new_status in self.TRANSITIONS.get(self.status, [])

    @property
    def is_expired_by_date(self):
        return self.expires_at <= timezone.now()

    @property
    def days_until_expiry(self):
        return (self.expires_at - timezone.now()).days


class TestResult(models.Model):
    """One laboratory test performed on a blood bag. Retest = additional row."""

    class ResultStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        NON_REACTIVE = "NON_REACTIVE", "Non-reactive / Acceptable"
        REACTIVE = "REACTIVE", "Reactive"
        INVALID = "INVALID", "Invalid"
        REQUIRES_RETEST = "REQUIRES_RETEST", "Requires retest"

    bag = models.ForeignKey(BloodBag, on_delete=models.PROTECT, related_name="test_results")
    test_type = models.ForeignKey(TestType, on_delete=models.PROTECT, related_name="results")
    result = models.CharField(max_length=120, blank=True, help_text="Raw result value, e.g. 'O RhD positive'")
    result_status = models.CharField(
        max_length=16, choices=ResultStatus.choices, default=ResultStatus.PENDING, db_index=True
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="tests_performed"
    )
    performed_at = models.DateTimeField(default=timezone.now)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tests_verified",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-performed_at"]

    def __str__(self):
        return f"{self.bag.bag_code} · {self.test_type.name} · {self.result_status}"

    @property
    def latest_for_bag_test(self):
        return (
            TestResult.objects.filter(bag=self.bag, test_type=self.test_type)
            .order_by("-performed_at", "-id")
            .first()
        )


class InventoryTransaction(models.Model):
    """Append-only inventory ledger. Every bag movement records one row, so a
    bag's full history can be reconstructed."""

    class TxnType(models.TextChoices):
        COLLECTION = "COLLECTION", "Collection"
        QUARANTINE = "QUARANTINE", "Quarantine"
        TESTING = "TESTING", "Testing"
        RELEASE = "RELEASE", "Authorized release"
        RESERVATION = "RESERVATION", "Reservation"
        ISSUE = "ISSUE", "Issue"
        RETURN = "RETURN", "Return"
        DISCARD = "DISCARD", "Discard"
        EXPIRATION = "EXPIRATION", "Expiration"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment / override"

    bag = models.ForeignKey(BloodBag, on_delete=models.PROTECT, related_name="transactions")
    transaction_type = models.CharField(max_length=12, choices=TxnType.choices)
    previous_status = models.CharField(max_length=12)
    new_status = models.CharField(max_length=12)
    quantity = models.PositiveIntegerField(default=1)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=120, blank=True, help_text="e.g. blood request code")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.bag.bag_code}: {self.previous_status} → {self.new_status} ({self.transaction_type})"

    def delete(self, *args, **kwargs):
        raise RuntimeError("Inventory ledger entries cannot be deleted.")


class CompatibilityRule(models.Model):
    """Configurable compatibility matrix.

    SAFETY: rows define which donor blood type may be considered for a patient
    blood type (optionally per component). The rule set MUST be supplied or
    approved by qualified institutional personnel — the application never
    invents clinical compatibility. Unconfigured pairs are NOT compatible.
    """

    patient_type = models.ForeignKey(BloodType, on_delete=models.CASCADE, related_name="compat_as_patient")
    donor_type = models.ForeignKey(BloodType, on_delete=models.CASCADE, related_name="compat_as_donor")
    component = models.ForeignKey(
        BloodComponent, on_delete=models.CASCADE, null=True, blank=True,
        help_text="Leave empty to apply to all components.",
    )
    is_allowed = models.BooleanField(default=True)
    note = models.CharField(max_length=255, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("patient_type", "donor_type", "component")
        ordering = ["patient_type__abo", "donor_type__abo"]

    def __str__(self):
        comp = f" / {self.component}" if self.component else ""
        allowed = "allowed" if self.is_allowed else "NOT allowed"
        return f"{self.patient_type} ← {self.donor_type}{comp}: {allowed}"
