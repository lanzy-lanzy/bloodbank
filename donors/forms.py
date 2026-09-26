from django import forms

from core.forms import StyledModelForm
from core.validators import validate_ph_mobile
from donors.models import Donor, DonorScreening, ScreeningQuestion


class DonorForm(StyledModelForm):
    """Staff/admin donor registration & editing."""

    contact_number = forms.CharField(max_length=30, label="Contact number (mobile)",
                                     validators=[validate_ph_mobile])

    class Meta:
        model = Donor
        fields = [
            "first_name", "middle_name", "last_name", "suffix", "date_of_birth", "sex",
            "contact_number", "email", "address", "municipality", "province",
            "emergency_contact_name", "emergency_contact_phone", "blood_type", "status",
        ]
        widgets = {"date_of_birth": forms.DateInput(attrs={"type": "date"})}


class DonorSelfProfileForm(StyledModelForm):
    """Donor self-service: only permitted personal information (no medical/status fields)."""

    contact_number = forms.CharField(max_length=30, label="Contact number (mobile)",
                                     validators=[validate_ph_mobile])

    class Meta:
        model = Donor
        fields = ["contact_number", "email", "address", "municipality", "province",
                  "emergency_contact_name", "emergency_contact_phone"]


class DonorStatusForm(StyledModelForm):
    class Meta:
        model = Donor
        fields = ["status", "deferral_reason", "deferred_until"]
        widgets = {"deferred_until": forms.DateInput(attrs={"type": "date"})}

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("status")
        if status in ("TEMP_DEFERRED", "PERM_DEFERRED", "BLACKLISTED") and not cleaned.get("deferral_reason"):
            self.add_error("deferral_reason", "A reason is required when deferring or restricting a donor.")
        if status == "TEMP_DEFERRED" and not cleaned.get("deferred_until"):
            self.add_error("deferred_until", "Temporary deferrals need an end date.")
        return cleaned


class ScreeningForm(StyledModelForm):
    class Meta:
        model = DonorScreening
        fields = ["identity_verified", "consent_given", "weight_kg", "temperature_c",
                  "bp_systolic", "bp_diastolic", "pulse_bpm", "hemoglobin_gdl",
                  "result", "deferral_days", "notes"]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("result") == "CLEARED" and not (cleaned.get("identity_verified") and cleaned.get("consent_given")):
            raise forms.ValidationError(
                "Identity verification and consent are required before a donor can be CLEARED."
            )
        if cleaned.get("result") == "DEFERRED" and not cleaned.get("deferral_days"):
            self.add_error("deferral_days", "Specify the staff-decided deferral length in days.")
        return cleaned


def question_forms(prefix="q"):
    """One simple form per active screening question (configurable questionnaire)."""
    forms_list = []
    for question in ScreeningQuestion.objects.filter(is_active=True):
        if question.answer_type == "YESNO":
            field = forms.ChoiceField(choices=[("", "—"), ("YES", "Yes"), ("NO", "No")],
                                      required=question.required, label=question.text)
        elif question.answer_type == "NUMBER":
            field = forms.CharField(required=question.required, label=question.text)
        elif question.answer_type == "CHOICE":
            field = forms.ChoiceField(
                choices=[("", "—")] + [(c, c) for c in question.choices],
                required=question.required, label=question.text)
        else:
            field = forms.CharField(required=question.required, label=question.text,
                                    widget=forms.Textarea(attrs={"rows": 2}))
        from core.forms import StyledFormMixin
        form_class = type(f"Q{question.pk}Form", (StyledFormMixin, forms.Form), {f"field_{question.pk}": field})
        forms_list.append({"question": question, "form": form_class(prefix=f"{prefix}{question.pk}")})
    return forms_list
