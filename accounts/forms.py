from django import forms
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from accounts.models import RegistrationInterview, RegistrationRequest, User
from core.forms import StyledFormMixin
from core.validators import validate_ph_mobile


class BloodBankAuthenticationForm(StyledFormMixin, AuthenticationForm):
    """Login form with failed-login lockout protection."""

    def confirm_login_allowed(self, user):
        if not user.is_active:
            reason = getattr(user, "deactivated_reason", "") or "This account is deactivated."
            raise ValidationError(reason, code="invalid_login")
        if user.is_locked_now:
            raise ValidationError(
                "This account is temporarily locked due to repeated failed login attempts. "
                "Please try again later or contact an administrator.",
                code="invalid_login",
            )

    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if username and password:
            candidate = User.objects.filter(username=username).first()
            if (candidate is not None and not candidate.is_active
                    and candidate.check_password(password)):
                # Self-registered accounts wait for admin approval; show the
                # applicant the real reason instead of a generic login failure.
                # Only after the password matches, so status is not leaked.
                reg = getattr(candidate, "registration_request", None)
                if reg is not None and reg.status == RegistrationRequest.Status.REJECTED:
                    reason = reg.rejection_reason or "No reason was provided."
                    raise ValidationError(
                        f"Your registration was rejected. Reason: {reason} "
                        "Contact the blood bank if you believe this is a mistake.",
                        code="registration_rejected",
                    )
                if reg is not None and reg.status == RegistrationRequest.Status.PENDING:
                    raise ValidationError(
                        "Your registration is pending admin approval. "
                        "You will be notified once it is reviewed.",
                        code="registration_pending",
                    )
                raise ValidationError(
                    getattr(candidate, "deactivated_reason", "") or "This account is deactivated.",
                    code="account_inactive",
                )
            self.user_cache = authenticate(self.request, username=username, password=password)
            if self.user_cache is None:
                if candidate and candidate.is_active:
                    candidate.record_failed_login(
                        settings.FAILED_LOGIN_LIMIT, settings.FAILED_LOGIN_LOCKOUT_SECONDS
                    )
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)
            self.user_cache.record_successful_login()
        return self.cleaned_data


class LoginForm(StyledFormMixin, forms.Form):
    username = forms.CharField(label="Username", max_length=150)
    password = forms.CharField(label="Password", widget=forms.PasswordInput(render_value=True))


class ProfileForm(StyledFormMixin, forms.ModelForm):
    phone = forms.CharField(max_length=30, required=True, label="Mobile number",
                            validators=[validate_ph_mobile],
                            help_text="Philippine mobile number for SMS notifications.")

    class Meta:
        model = User
        fields = ["first_name", "middle_name", "last_name", "email", "phone"]


class AdminUserForm(StyledFormMixin, forms.ModelForm):
    """Admin-managed user record. Password is never shown or set here."""

    phone = forms.CharField(max_length=30, required=True, label="Mobile number",
                            validators=[validate_ph_mobile],
                            help_text="Philippine mobile number for SMS notifications.")

    class Meta:
        model = User
        fields = ["username", "first_name", "middle_name", "last_name", "email", "phone",
                  "role", "is_active", "is_locked"]

    def clean(self):
        cleaned = super().clean()
        # Never allow an admin to demote/lock the last active admin.
        instance_user = self.instance
        if instance_user and instance_user.pk:
            becoming_non_admin = (
                cleaned.get("role") != "ADMIN"
                or not cleaned.get("is_active")
                or cleaned.get("is_locked")
            )
            if instance_user.role == "ADMIN" and becoming_non_admin:
                other_admins = User.objects.filter(
                    role="ADMIN", is_active=True, is_locked=False
                ).exclude(pk=instance_user.pk).exists()
                if not other_admins:
                    raise forms.ValidationError(
                        "Cannot remove admin access from the last active administrator."
                    )
        return cleaned


class AdminUserCreateForm(AdminUserForm):
    password1 = forms.CharField(label="Temporary password", widget=forms.PasswordInput, strip=False)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput, strip=False)

    class Meta(AdminUserForm.Meta):
        fields = AdminUserForm.Meta.fields  # same + password fields declared explicitly

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class StyledPasswordChangeForm(StyledFormMixin, PasswordChangeForm):
    pass


class RegistrationForm(StyledFormMixin, forms.ModelForm):
    """Public self-registration (DONOR or REQUESTER only).

    The account is created inactive via the service layer; role choices hard-
    exclude ADMIN/STAFF so a crafted POST can never select an elevated role."""

    password1 = forms.CharField(label="Password", widget=forms.PasswordInput,
                                help_text="Minimum 8 characters; avoid all-numeric passwords.")
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput)

    class Meta:
        model = RegistrationRequest
        fields = ["role", "first_name", "middle_name", "last_name", "username", "email",
                  "phone", "blood_type", "date_of_birth", "address", "municipality",
                  "province", "organization_name"]
        widgets = {"date_of_birth": forms.DateInput(attrs={"type": "date"})}
        labels = {"blood_type": "Blood type (optional)", "organization_name": "Organization"}
        help_texts = {"organization_name": "Required for requesters (hospital/clinic/organization)."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from inventory.models import BloodType
        self.fields["blood_type"].queryset = BloodType.objects.filter(is_active=True)
        self.fields["blood_type"].required = False
        self.fields["blood_type"].empty_label = "—"
        # Donor interview sheet: a YES/NO radio per question + a certification
        # checkbox. Added as form fields (not on the RegistrationRequest model)
        # so a single POST drives both records. required is enforced per-role
        # in clean() (only for DONOR); at field level they stay optional so a
        # REQUESTER submit without them is still valid.
        for name in RegistrationInterview.QUESTION_FIELDS:
            label = RegistrationInterview._meta.get_field(name).verbose_name
            self.fields[name] = forms.ChoiceField(
                label=label, required=False, widget=forms.RadioSelect,
                choices=[("yes", "Yes"), ("no", "No")])
        self.fields["declaration"] = forms.BooleanField(
            label=str(RegistrationInterview._meta.get_field("declaration").verbose_name),
            required=False)
        self.fields["interview_notes"] = forms.CharField(
            label="Additional notes (optional)", required=False, widget=forms.Textarea,
            help_text="Anything the blood bank should know (allergies, recent illness, etc.).")
        # Style the dynamically-added widgets (StyledFormMixin ran before they
        # existed), so radios/checkbox/textarea match the rest of the form.
        from core.forms import INPUT_CLASSES
        for name in RegistrationInterview.QUESTION_FIELDS:
            self.fields[name].widget.attrs["class"] = "space-y-1"
        self.fields["declaration"].widget.attrs["class"] = (
            "rounded border-ink-300 text-brand-600 focus:ring-brand-500/40 h-4 w-4")
        self.fields["interview_notes"].widget.attrs["class"] = INPUT_CLASSES

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if not username or any(c.isspace() for c in username):
            raise ValidationError("Username is required and cannot contain spaces.")
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError("That username is already taken.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email already exists.")
        return email

    def clean_phone(self):
        phone = self.cleaned_data["phone"]
        validate_ph_mobile(phone)  # required so the approval SMS can reach the applicant
        return phone

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        if role == User.Role.DONOR:
            for key, label in (("date_of_birth", "Date of birth"), ("address", "Address"),
                               ("municipality", "Municipality")):
                if not cleaned.get(key):
                    self.add_error(key, f"{label} is required for donors.")
            # Donor interview sheet is mandatory for donors. Normalise the
            # "yes"/"no" radio values to real booleans in cleaned_data so the
            # service can persist them; a missing answer is a validation error.
            for key in RegistrationInterview.QUESTION_FIELDS:
                raw = cleaned.get(key)
                if raw in ("yes", "no"):
                    cleaned[key] = (raw == "yes")
                else:
                    cleaned[key] = None
                    self.add_error(key, "Please answer this question.")
            if not cleaned.get("declaration"):
                self.add_error("declaration",
                               "You must certify the interview before submitting.")
        if role == User.Role.REQUESTER and not (cleaned.get("organization_name") or "").strip():
            self.add_error("organization_name", "Organization is required for a requester.")
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        elif p1:
            probe = User(username=cleaned.get("username", ""), email=cleaned.get("email", ""))
            try:
                validate_password(p1, probe)
            except ValidationError as exc:
                self.add_error("password1", exc.messages)
        return cleaned

    def interview_question_fields(self):
        """Bound fields for the interview questions, in questionnaire order."""
        return [self[name] for name in RegistrationInterview.QUESTION_FIELDS]

    def interview_answers(self):
        """YES/NO answers + declaration for a DONOR interview, or None.

        Only meaningful once the form is valid and role is DONOR; the values in
        cleaned_data were normalised to booleans in clean(). Returned as a plain
        dict so the service can build the RegistrationInterview without knowing
        anything about the form."""
        if self.cleaned_data.get("role") != User.Role.DONOR:
            return None
        answers = {key: self.cleaned_data.get(key)
                   for key in RegistrationInterview.QUESTION_FIELDS}
        answers["declaration"] = bool(self.cleaned_data.get("declaration"))
        answers["notes"] = (self.cleaned_data.get("interview_notes") or "").strip()
        return answers
