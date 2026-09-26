"""Blood-request domain: organizations, requester profiles, requests, items, allocations."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone


def next_request_code():
    year = timezone.now().year
    last = (
        BloodRequest.objects.filter(request_code__startswith=f"BR-{year}-")
        .order_by("-request_code").values_list("request_code", flat=True).first()
    )
    seq = int(last.split("-")[-1]) + 1 if last else 1
    return f"BR-{year}-{seq:06d}"


class Organization(models.Model):
    class OrgType(models.TextChoices):
        HOSPITAL = "HOSPITAL", "Hospital"
        CLINIC = "CLINIC", "Clinic"
        RHU = "RHU", "Rural Health Unit (RHU)"
        EMERGENCY = "EMERGENCY", "Emergency facility"
        ORGANIZATION = "ORGANIZATION", "Authorized organization"
        OTHER = "OTHER", "Other approved entity"

    name = models.CharField(max_length=180, unique=True)
    org_type = models.CharField(max_length=12, choices=OrgType.choices, default=OrgType.HOSPITAL)
    address = models.CharField(max_length=255, blank=True)
    contact_number = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    license_number = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class RequesterProfile(models.Model):
    """Authorized user account linked to an organization."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="requester_profile")
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="members")
    position = models.CharField(max_length=80, blank=True)
    is_authorized = models.BooleanField(default=True, help_text="Authorization verified by staff/admin.")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} @ {self.organization.name}"


class BloodRequest(models.Model):
    class Urgency(models.TextChoices):
        ROUTINE = "ROUTINE", "Routine"
        URGENT = "URGENT", "Urgent"
        EMERGENCY = "EMERGENCY", "Emergency"
        CRITICAL = "CRITICAL", "Critical"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted"
        UNDER_REVIEW = "UNDER_REVIEW", "Under review"
        APPROVED = "APPROVED", "Approved"
        PARTIALLY_FULFILLED = "PARTIALLY_FULFILLED", "Partially fulfilled"
        FULFILLED = "FULFILLED", "Fulfilled"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"
        EXPIRED = "EXPIRED", "Expired"

    OPEN_STATUSES = ["DRAFT", "SUBMITTED", "UNDER_REVIEW", "APPROVED", "PARTIALLY_FULFILLED"]

    request_code = models.CharField(max_length=20, unique=True, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="requests")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   related_name="requests_created")
    patient_reference = models.CharField(
        max_length=120, blank=True,
        help_text="Patient identifier/reference. Keep to the minimum necessary — no clinical details in free text.",
    )
    required_by = models.DateTimeField(help_text="Date/time blood is needed.")
    urgency = models.CharField(max_length=10, choices=Urgency.choices, default=Urgency.ROUTINE, db_index=True)
    clinical_indication = models.CharField(max_length=255, blank=True)
    supporting_document = models.FileField(
        upload_to="request_docs/", blank=True, null=True,
        validators=[FileExtensionValidator(["pdf", "png", "jpg", "jpeg", "doc", "docx"])],
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    assigned_staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                       blank=True, related_name="requests_assigned")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name="requests_approved")
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    cancellation_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "urgency"])]

    def __str__(self):
        return f"{self.request_code} — {self.organization.name}"

    def save(self, *args, **kwargs):
        if not self.request_code:
            self.request_code = next_request_code()
        super().save(*args, **kwargs)

    def clean(self):
        if self.required_by and self.required_by <= timezone.now() and self.status in ("DRAFT", "SUBMITTED"):
            raise ValidationError({"required_by": "Required date/time must be in the future for new requests."})

    @property
    def is_emergency(self):
        return self.urgency in ("EMERGENCY", "CRITICAL")

    @property
    def total_quantity(self):
        return sum(i.quantity for i in self.items.all())

    @property
    def total_fulfilled(self):
        return sum(i.fulfilled_quantity for i in self.items.all())


class RequestItem(models.Model):
    request = models.ForeignKey(BloodRequest, on_delete=models.CASCADE, related_name="items")
    blood_type = models.ForeignKey("inventory.BloodType", on_delete=models.PROTECT)
    component = models.ForeignKey("inventory.BloodComponent", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    fulfilled_quantity = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.quantity} × {self.blood_type} {self.component}"

    def clean(self):
        if self.quantity < 1:
            raise ValidationError("Quantity must be at least 1.")

    @property
    def outstanding(self):
        return max(0, self.quantity - self.fulfilled_quantity)


class Allocation(models.Model):
    """Links a specific blood bag to a request item: RESERVED → ISSUED (fulfillment).

    Fulfilment is recorded here (issued_at/by) rather than as a separate table;
    see DECISIONS.md.
    """

    class Status(models.TextChoices):
        RESERVED = "RESERVED", "Reserved"
        ISSUED = "ISSUED", "Issued"
        RETURNED = "RETURNED", "Returned"
        CANCELLED = "CANCELLED", "Cancelled"

    ACTIVE_STATUSES = ["RESERVED", "ISSUED"]

    request = models.ForeignKey(BloodRequest, on_delete=models.PROTECT, related_name="allocations")
    item = models.ForeignKey(RequestItem, on_delete=models.PROTECT, related_name="allocations")
    bag = models.ForeignKey("inventory.BloodBag", on_delete=models.PROTECT, related_name="allocations")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RESERVED, db_index=True)
    allocated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                     related_name="allocations_made")
    allocated_at = models.DateTimeField(auto_now_add=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                  blank=True, related_name="allocations_issued")
    issued_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-allocated_at"]
        constraints = [
            # A bag may have at most one ACTIVE allocation at a time.
            models.UniqueConstraint(
                fields=["bag"],
                condition=models.Q(status__in=["RESERVED", "ISSUED"]),
                name="one_active_allocation_per_bag",
            ),
        ]

    def __str__(self):
        return f"{self.bag.bag_code} → {self.request.request_code} ({self.status})"
