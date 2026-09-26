from django import forms

from core.forms import StyledFormMixin, StyledModelForm
from inventory.models import BloodBag, TestResult, TestType


class BagRegisterForm(StyledFormMixin, forms.Form):
    """Manual registration of an externally sourced bag (audited)."""

    blood_type = forms.ModelChoiceField(queryset=None)
    component = forms.ModelChoiceField(queryset=None)
    volume_ml = forms.IntegerField(min_value=1, max_value=1000, initial=450)
    collected_at = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
    )
    donor = forms.ModelChoiceField(queryset=None, required=False,
                                   help_text="Optional donor reference if known.")
    location = forms.CharField(required=False, max_length=120)
    storage_position = forms.CharField(required=False, max_length=60)
    reason = forms.CharField(max_length=255, help_text="Why is this bag being registered without a donation record?")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from donors.models import Donor
        from inventory.models import BloodComponent, BloodType
        from django.utils import timezone
        self.fields["blood_type"].queryset = BloodType.objects.filter(is_active=True)
        self.fields["component"].queryset = BloodComponent.objects.filter(is_active=True)
        self.fields["donor"].queryset = Donor.objects.all()
        if not self.is_bound:
            self.fields["collected_at"].initial = timezone.localtime().strftime("%Y-%m-%dT%H:%M")


class TestResultForm(StyledModelForm):
    class Meta:
        model = TestResult
        fields = ["test_type", "result", "result_status", "performed_at", "notes"]
        widgets = {
            "performed_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["test_type"].queryset = TestType.objects.filter(is_active=True)

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("result_status")
        if status == TestResult.ResultStatus.NON_REACTIVE and not cleaned.get("result"):
            self.add_error("result", "Record the actual result value for acceptable/non-reactive results.")
        return cleaned


class TransitionForm(StyledFormMixin, forms.Form):
    new_status = forms.ChoiceField(choices=[])
    reason = forms.CharField(required=False, max_length=255)

    def __init__(self, *args, bag=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.bag = bag
        if bag:
            allowed = BloodBag.TRANSITIONS.get(bag.status, [])
            # AVAILABLE via release is a distinct, guarded action — keep it out of the generic list.
            self.fields["new_status"].choices = [
                (s, s.replace("_", " ").title()) for s in allowed if s != "AVAILABLE"
            ]
