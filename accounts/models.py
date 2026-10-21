from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

from donors.interview import SIGNUP_FIELDS


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
    class Sex(models.TextChoices):
        FEMALE = "FEMALE", "Female"
        MALE = "MALE", "Male"
        OTHER = "OTHER", "Other"
        UNDISCLOSED = "UNDISCLOSED", "Prefer not to say"

    sex = models.CharField(max_length=12, choices=Sex.choices,
                           default=Sex.UNDISCLOSED)
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

    The questionnaire is the printed sheet's own questionnaire (donors/interview.py):
    one YES/NO field per YES/NO item, in the sheet's order, so an answer is copied
    to the item it answers and nothing is inferred. The two date items are not
    asked at sign-up; the female block only applies to female applicants.
    """

    registration = models.OneToOneField(
        RegistrationRequest, on_delete=models.CASCADE, related_name="interview")

    # --- legacy 11-question form (no longer asked or displayed) --------------
    # Superseded by the q.. fields below. The columns stay so answers already on
    # file are never destroyed (agents.md rule 4); the data migration copies the
    # exact-match answers into their q.. field. Never render or require these.
    felt_well_today = models.BooleanField(null=True, verbose_name=(
        "I feel well and healthy today."))
    on_medication = models.BooleanField(null=True, verbose_name=(
        "In the past 4 weeks I have taken any medications."))
    recent_illness = models.BooleanField(null=True, verbose_name=(
        "I have had fever, flu, cold, cough, or infection within the past 2 weeks."))
    prior_transfusion = models.BooleanField(null=True, verbose_name=(
        "In the past 12 months I have received blood, blood products, and/or had a "
        "tissue or organ transplant or graft."))
    reactive_test = models.BooleanField(null=True, verbose_name=(
        "I have ever had a positive test for Hepatitis B/C, HIV, Syphilis, or Malaria."))
    tattoo_piercing = models.BooleanField(null=True, verbose_name=(
        "In the past 12 months I have had a tattoo, ear or body piercing, acupuncture, "
        "needle stick injury, or accidental contact with blood."))
    dental_procedure = models.BooleanField(null=True, verbose_name=(
        "In the past 12 months I have had a surgical operation or dental extraction."))
    recent_vaccination = models.BooleanField(null=True, verbose_name=(
        "In the past 4 weeks I have been vaccinated."))
    high_risk_behavior = models.BooleanField(null=True, verbose_name=(
        "In the past 12 months I have had sexual contact with high-risk individuals or "
        "in exchange for material or monetary gain."))
    pregnant_or_breastfeeding = models.BooleanField(null=True, verbose_name=(
        "I am currently pregnant, breastfeeding, or delivered within the past year."))
    previously_deferred = models.BooleanField(null=True, verbose_name=(
        "I have ever been refused as a blood donor or told not to donate blood for "
        "any reason."))

    # --- the printed questionnaire, answered at sign-up ---------------------
    # One nullable YES/NO field per YES/NO item of the PRC sheet, named after
    # the item key (Q07 -> q07). Labels are the sheet's own questions, so the
    # tick on the sheet and the answer on the form are the same question - no
    # mapping judgement is made anywhere. The two DATE items (Q34 last
    # childbirth, Q37 last menstrual period) have no tick box and are not asked
    # at sign-up; the female block is asked only when the applicant is female.
    q01 = models.BooleanField(null=True, verbose_name="1. Do you feel well and healthy today?")
    q02 = models.BooleanField(null=True, verbose_name=(
        "2. Have you ever been refused as a blood donor or told not to donate blood "
        "for any reasons?"))
    q03 = models.BooleanField(null=True, verbose_name=(
        "3. Are you giving blood only because you want to be tested for HIV or the "
        "AIDS virus or Hepatitis virus?"))
    q04 = models.BooleanField(null=True, verbose_name=(
        "4. Are you aware that an HIV / Hepatitis infected person can still transmit "
        "the virus despite a negative HIV / Hepatitis test?"))
    q05 = models.BooleanField(null=True, verbose_name=(
        "5. Have you within the last 12 HOURS had taken liquor, beer or any drinks "
        "with alcohol?"))
    q06 = models.BooleanField(null=True, verbose_name=(
        "6. In the last 3 DAYS have you taken aspirin?"))
    q07 = models.BooleanField(null=True, verbose_name=(
        "7. In the past 4 WEEKS have you taken any medications and/or vaccinations?"))
    q08 = models.BooleanField(null=True, verbose_name=(
        "8. In the past 3 MONTHS have you donated whole blood, platelets or plasma?"))
    q09 = models.BooleanField(null=True, verbose_name=(
        "9. In the past 6 MONTHS have you been to any places in the Philippines or "
        "countries infected with ZIKA Virus?"))
    q10 = models.BooleanField(null=True, verbose_name=(
        "10. In the past 6 MONTHS have you had sexual contact with a person who was "
        "confirmed to have ZIKA Virus infection?"))
    q11 = models.BooleanField(null=True, verbose_name=(
        "11. In the past 6 MONTHS have you had sexual contact with a person who has "
        "been to any places in the Philippines or countries infected with ZIKA Virus?"))
    q12 = models.BooleanField(null=True, verbose_name=(
        "12. In the past 12 MONTHS have you received blood, blood products and/or had "
        "tissue/organ transplant or graft?"))
    q13 = models.BooleanField(null=True, verbose_name=(
        "13. In the past 12 MONTHS have you had a surgical operation or dental extraction?"))
    q14 = models.BooleanField(null=True, verbose_name=(
        "14. In the past 12 MONTHS have you had a tattoo applied, ear and body piercing, "
        "acupuncture, needle stick injury or accidental contact with blood?"))
    q15 = models.BooleanField(null=True, verbose_name=(
        "15. In the past 12 MONTHS have you had sexual contact with high risks "
        "individuals or in exchange for material or monetary gain?"))
    q16 = models.BooleanField(null=True, verbose_name=(
        "16. In the past 12 MONTHS have you engaged in unprotected, unsafe or casual sex?"))
    q17 = models.BooleanField(null=True, verbose_name=(
        "17. In the past 12 MONTHS have you had jaundice/hepatitis/personal contact "
        "with a person who had hepatitis?"))
    q18 = models.BooleanField(null=True, verbose_name=(
        "18. In the past 12 MONTHS have you been incarcerated, jailed or imprisoned?"))
    q19 = models.BooleanField(null=True, verbose_name=(
        "19. In the past 12 MONTHS have you spent time in the United Kingdom or Europe, "
        "or do you have relatives there?"))
    q20 = models.BooleanField(null=True, verbose_name=(
        "20. Have you ever travelled or lived outside of your place of residence or "
        "outside the Philippines?"))
    q21 = models.BooleanField(null=True, verbose_name=(
        "21. Have you ever taken prohibited drugs (orally, by nose, or by injection)?"))
    q22 = models.BooleanField(null=True, verbose_name=(
        "22. Have you ever used clotting factor concentrates?"))
    q23 = models.BooleanField(null=True, verbose_name=(
        "23. Have you ever had a positive test for the HIV virus, Hepatitis virus, "
        "Syphilis or Malaria?"))
    q24 = models.BooleanField(null=True, verbose_name=(
        "24. Have you ever had Malaria or Hepatitis in the past?"))
    q25 = models.BooleanField(null=True, verbose_name=(
        "25. Have you ever had or been treated for genital wart, syphilis, gonorrhea "
        "or other sexually transmitted diseases?"))
    q26 = models.BooleanField(null=True, verbose_name=(
        "26. Have you had cancer, blood disease or bleeding disorder (haemophilia)?"))
    q27 = models.BooleanField(null=True, verbose_name=(
        "27. Have you had heart disease/surgery, rheumatic fever or chest pains?"))
    q28 = models.BooleanField(null=True, verbose_name=(
        "28. Have you had lung disease, tuberculosis or asthma?"))
    q29 = models.BooleanField(null=True, verbose_name=(
        "29. Have you had kidney disease, thyroid disease, diabetes, epilepsy?"))
    q30 = models.BooleanField(null=True, verbose_name=(
        "30. Have you had chicken pox and/or cold sores?"))
    q31 = models.BooleanField(null=True, verbose_name=(
        "31. Have you had any other chronic medical condition or surgical operations?"))
    q32 = models.BooleanField(null=True, verbose_name=(
        "32. Have you recently had rash and/or fever? Was/were this/these also "
        "associated with arthralgia or arthritis or conjunctivitis?"))
    q33 = models.BooleanField(null=True, verbose_name=(
        "33. FOR FEMALE DONORS: are you currently pregnant or have you ever been "
        "pregnant?"))
    q35 = models.BooleanField(null=True, verbose_name=(
        "35. FOR FEMALE DONORS: in the past 1 YEAR did you have a miscarriage or "
        "abortion?"))
    q36 = models.BooleanField(null=True, verbose_name=(
        "36. FOR FEMALE DONORS: are you currently breastfeeding?"))

    # applicant certification (required) + optional free-text context
    declaration = models.BooleanField(default=False, verbose_name=(
        "I certify that the information above is true and complete to the best of my "
        "knowledge, and I understand it will be reviewed by blood bank staff."))
    notes = models.TextField(blank=True, verbose_name="Additional notes (optional)")

    created_at = models.DateTimeField(auto_now_add=True)

    # Ordered question field names for rendering / review display: one per
    # YES/NO item of the printed sheet, in sheet order (derived from
    # donors.interview, never typed twice).
    QUESTION_FIELDS = list(SIGNUP_FIELDS)

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
