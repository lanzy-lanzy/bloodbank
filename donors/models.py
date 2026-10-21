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
    def registered_address(self):
        """Street, municipality and province as one line, without repeats.

        The printed interview sheet has a single free-text street box, while
        the record keeps the parts separate; joining them here keeps every
        printable document consistent with the form.
        """
        parts = []
        for value in (self.address, self.municipality, self.province):
            value = (value or "").strip()
            if value and value.casefold() not in ", ".join(parts).casefold():
                parts.append(value)
        return ", ".join(parts)

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


class DonorInterviewSheet(models.Model):
    """The printable PRC / DOH Blood Donor Interview Sheet for one donor.

    Generated automatically when a donor record is created (i.e. when an
    administrator approves a donor registration, or staff register a donor at
    the counter) and pre-filled from data already on file: identity, address,
    contact details and the declaration the applicant signed at sign-up.

    It is a RECORD of what the donor declared and what the interviewer
    confirmed. It is deliberately NOT an eligibility engine: no answer is
    scored, no deferral period is derived, and completing a sheet never
    changes the donor's status. Clinical decisions stay with staff and the
    configured screening tables (agents.md rule 1).
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft - interview in progress"
        COMPLETED = "COMPLETED", "Completed and signed"

    class CueChoice(models.TextChoices):
        SAFE = "SAFE", "SAFE for transfusion"
        NOT_SAFE = "NOT_SAFE", "NOT SAFE for transfusion"

    class CivilStatus(models.TextChoices):
        SINGLE = "SINGLE", "Single"
        MARRIED = "MARRIED", "Married"
        WIDOWED = "WIDOWED", "Widowed"
        SEPARATED = "SEPARATED", "Separated"
        ANNULLED = "ANNULLED", "Annulled"

    donor = models.OneToOneField(Donor, on_delete=models.CASCADE,
                                 related_name="interview_sheet")
    # Provenance: the self-registration this sheet was generated from, when
    # there was one. Staff-registered donors leave it null.
    registration = models.OneToOneField(
        "accounts.RegistrationRequest", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="interview_sheet")

    # --- header fields the donor record does not hold -----------------------
    prc_donor_number = models.CharField(max_length=40, blank=True, verbose_name="PRC blood donor number")
    doh_barcode = models.CharField(max_length=80, blank=True, verbose_name="DOH NBBets barcode")
    civil_status = models.CharField(max_length=12, choices=CivilStatus.choices, blank=True)
    nationality = models.CharField(max_length=60, blank=True)
    religion = models.CharField(max_length=60, blank=True)
    education = models.CharField(max_length=80, blank=True)
    occupation = models.CharField(max_length=80, blank=True)
    office_address = models.CharField(max_length=255, blank=True)
    address_no = models.CharField(max_length=60, blank=True, verbose_name="House / lot no.")
    address_street = models.CharField(max_length=150, blank=True, verbose_name="Street")
    address_barangay = models.CharField(max_length=120, blank=True, verbose_name="Barangay")
    address_zip = models.CharField(max_length=10, blank=True, verbose_name="Zip code")
    telephone_number = models.CharField(max_length=30, blank=True, verbose_name="Telephone no.")
    id_school = models.CharField(max_length=60, blank=True, verbose_name="School ID")
    id_company = models.CharField(max_length=60, blank=True, verbose_name="Company ID")
    id_prc = models.CharField(max_length=60, blank=True, verbose_name="PRC ID")
    id_drivers = models.CharField(max_length=60, blank=True, verbose_name="Driver's licence")
    id_sss = models.CharField(max_length=60, blank=True, verbose_name="SSS / GSIS / BIR")
    id_others = models.CharField(max_length=60, blank=True, verbose_name="Other ID")

    # --- Confidential Unit Exclusion ----------------------------------------
    cue_choice = models.CharField(max_length=8, choices=CueChoice.choices, blank=True,
                                  verbose_name="Donor's CUE declaration")
    cue_unit_serial = models.CharField(max_length=60, blank=True)
    cue_date_of_donation = models.DateField(null=True, blank=True)
    cue_place_of_donation = models.CharField(max_length=160, blank=True)

    # --- signatures ---------------------------------------------------------
    donor_signature = models.CharField(max_length=120, blank=True, verbose_name="Donor signature (printed name)")
    donor_signed_on = models.DateField(null=True, blank=True)
    interviewer_signature = models.CharField(max_length=120, blank=True,
                                             verbose_name="Interviewer signature (printed name)")
    interviewer_signed_on = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT,
                              db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name="interview_sheets_completed")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Donor interview sheet"
        verbose_name_plural = "Donor interview sheets"

    def __str__(self):
        return f"Interview sheet - {self.donor.donor_code}"

    @property
    def is_completed(self):
        return self.status == self.Status.COMPLETED

    def answer_map(self):
        """{item key: InterviewAnswer} for this sheet."""
        return {answer.item_key: answer for answer in self.answers.all()}

    def answer_for(self, item_key):
        """'YES' | 'NO' | '' for one item (no answer is not a NO)."""
        answer = self.answers.filter(item_key=item_key).first()
        return answer.answer if answer else ""


class InterviewAnswer(models.Model):
    """The donor's answer and the interviewer's remarks for one sheet item."""

    class Answer(models.TextChoices):
        YES = "YES", "Yes"
        NO = "NO", "No"

    sheet = models.ForeignKey(DonorInterviewSheet, on_delete=models.CASCADE,
                              related_name="answers")
    item_key = models.CharField(max_length=8)  # stable key from donors.interview
    answer = models.CharField(max_length=3, choices=Answer.choices, blank=True)
    remarks = models.TextField(blank=True)

    class Meta:
        unique_together = ("sheet", "item_key")
        ordering = ["item_key"]

    def __str__(self):
        return f"{self.sheet_id} {self.item_key}: {self.answer or '-'}"
