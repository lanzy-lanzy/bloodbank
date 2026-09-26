from django import forms
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.core.exceptions import ValidationError

from accounts.models import User
from core.forms import StyledFormMixin


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
    class Meta:
        model = User
        fields = ["first_name", "middle_name", "last_name", "email", "phone"]


class AdminUserForm(StyledFormMixin, forms.ModelForm):
    """Admin-managed user record. Password is never shown or set here."""

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
