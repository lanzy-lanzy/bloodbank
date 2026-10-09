from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """Custom user with a single primary role.

    Roles map to the four confirmed personas: ADMIN, STAFF, DONOR, REQUESTER.
    Fine-grained Django permissions may additionally be granted per user/group.
    """

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        STAFF = "STAFF", "Staff"
        DONOR = "DONOR", "Donor"
        REQUESTER = "REQUESTER", "Requester"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STAFF, db_index=True)
    phone = models.CharField(max_length=30, blank=True)
    middle_name = models.CharField(max_length=60, blank=True)

    # Account status / failed-login protection
    is_locked = models.BooleanField(default=False)
    locked_until = models.DateTimeField(null=True, blank=True)
    failed_login_count = models.PositiveIntegerField(default=0)
    deactivated_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["username"]

    # --- helpers -------------------------------------------------------------
    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        name = " ".join(p for p in parts if p)
        return name or self.username

    def get_initials(self):
        name = self.full_name
        pieces = [p for p in name.split() if p]
        if len(pieces) >= 2:
            return (pieces[0][0] + pieces[-1][0]).upper()
        return name[:2].upper()

    @property
    def is_locked_now(self):
        if self.locked_until and self.locked_until > timezone.now():
            return True
        return self.is_locked

    def record_failed_login(self, limit: int, lockout_seconds: int):
        self.failed_login_count += 1
        if self.failed_login_count >= limit:
            self.locked_until = timezone.now() + timezone.timedelta(seconds=lockout_seconds)
            self.failed_login_count = 0
        self.save(update_fields=["failed_login_count", "locked_until"])

    def record_successful_login(self):
        self.failed_login_count = 0
        self.locked_until = None
        self.is_locked = False
        self.save(update_fields=["failed_login_count", "locked_until", "is_locked"])

    def has_role(self, *roles):
        return self.role in roles


class RegistrationRequest(models.Model):
    """Public self-registration (DONOR or REQUESTER) awaiting admin review.

    The linked User is created immediately but inactive; approval flips
    is_active, rejection leaves it blocked. State machine:
    PENDING -> APPROVED | REJECTED, one-shot (append-only review fields)."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    TRANSITIONS = {Status.PENDING: {Status.APPROVED, Status.REJECTED}}

    class EligibleRole(models.TextChoices):
        # Deliberately excludes ADMIN/STAFF: self-registration can never
        # produce an elevated role (enforced in form AND service).
        DONOR = "DONOR", "Donor"
        REQUESTER = "REQUESTER", "Requester"

    # identity
    first_name = models.CharField(max_length=150)
    middle_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150)
    username = models.CharField(max_length=150)
    email = models.EmailField()
    phone = models.CharField(max_length=30)
    role = models.CharField(max_length=20, choices=EligibleRole.choices)

    # donor-specific
    blood_type = models.ForeignKey("inventory.BloodType", on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="+")
    date_of_birth = models.DateField(null=True, blank=True)
    address = models.CharField(max_length=255, blank=True)
    municipality = models.CharField(max_length=120, blank=True)
    province = models.CharField(max_length=120, blank=True)

    # requester-specific
    organization_name = models.CharField(max_length=200, blank=True)

    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="registration_request")

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING,
                              db_index=True)
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="registrations_reviewed")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.role} registration — {self.username} ({self.status})"

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p) or self.username


class RegistrationInterview(models.Model):
    """Self-declared donor interview sheet filled during DONOR registration.

    Mirrors the standard pre-donation health self-declaration (Philippine Red
    Cross / WHO donor interview style): a fixed set of YES/NO questions plus an
    applicant declaration. It is a RECORD of what the applicant stated at signup
    so a blood bank administrator can weigh it when approving the registration.

    It is deliberately NOT an eligibility engine: no clinical threshold,
    deferral period, or auto-approve/auto-reject rule is computed here (see
    agents.md rule 1 — clinical rules live with staff and the DB screening
    tables). The formal, institution-configurable screening that produces a
    pass/defer decision lives in `donors.ScreeningQuestion` /
    `donors.DonorScreening`, run at collection time against an approved Donor.
    """

    registration = models.OneToOneField(
        RegistrationRequest, on_delete=models.CASCADE, related_name="interview")

    # --- YES/NO self-declarations (null = not answered) ----------------------
    felt_well_today = models.BooleanField(null=True, verbose_name=(
        "I feel well and healthy today."))
    on_medication = models.BooleanField(null=True, verbose_name=(
        "I am currently taking any medication."))
    recent_illness = models.BooleanField(null=True, verbose_name=(
        "I have had fever, flu, cold, cough, or infection within the past 2 weeks."))
    prior_transfusion = models.BooleanField(null=True, verbose_name=(
        "I have received a blood transfusion at any time in my life."))
    reactive_test = models.BooleanField(null=True, verbose_name=(
        "I have ever tested positive for Hepatitis B/C, HIV, Syphilis, or had Malaria."))
    tattoo_piercing = models.BooleanField(null=True, verbose_name=(
        "I have had a tattoo, body piercing, or acupuncture within the past 12 months."))
    dental_procedure = models.BooleanField(null=True, verbose_name=(
        "I have had a tooth extraction or dental procedure within the past 72 hours."))
    recent_vaccination = models.BooleanField(null=True, verbose_name=(
        "I have been vaccinated within the past 4 weeks."))
    high_risk_behavior = models.BooleanField(null=True, verbose_name=(
        "Within the past 12 months I have shared needles or had sexual contact with a "
        "partner at risk for HIV/Hepatitis."))
    pregnant_or_breastfeeding = models.BooleanField(null=True, verbose_name=(
        "I am currently pregnant, breastfeeding, or delivered within the past year."))
    previously_deferred = models.BooleanField(null=True, verbose_name=(
        "I have ever been deferred or turned away from donating blood."))

    # applicant certification (required) + optional free-text context
    declaration = models.BooleanField(default=False, verbose_name=(
        "I certify that the information above is true and complete to the best of my "
        "knowledge, and I understand it will be reviewed by blood bank staff."))
    notes = models.TextField(blank=True, verbose_name="Additional notes (optional)")

    created_at = models.DateTimeField(auto_now_add=True)

    # Ordered question field names for rendering / review display.
    QUESTION_FIELDS = [
        "felt_well_today", "on_medication", "recent_illness", "prior_transfusion",
        "reactive_test", "tattoo_piercing", "dental_procedure", "recent_vaccination",
        "high_risk_behavior", "pregnant_or_breastfeeding", "previously_deferred",
    ]

    class Meta:
        verbose_name = "Registration donor interview"
        verbose_name_plural = "Registration donor interviews"

    def __str__(self):
        return f"Donor interview — {self.registration.username}"

    def answered_questions(self):
        """[(label, 'Yes'|'No'|'—')] in questionnaire order, for the review page."""
        rows = []
        for name in self.QUESTION_FIELDS:
            value = getattr(self, name)
            answer = "Yes" if value is True else "No" if value is False else "—"
            rows.append((self._meta.get_field(name).verbose_name, answer))
        return rows
