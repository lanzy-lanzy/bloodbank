from django import forms

from core.forms import StyledFormMixin, StyledModelForm
from donations.models import Donation
from inventory.models import BloodComponent


class DonationForm(StyledModelForm):
    class Meta:
        model = Donation
        fields = ["donor", "donation_date", "donation_time", "location", "donation_type", "notes"]
        widgets = {
            "donation_date": forms.DateInput(attrs={"type": "date"}),
            "donation_time": forms.TimeInput(attrs={"type": "time"}),
        }


class CollectionForm(StyledFormMixin, forms.Form):
    """Blood collection: creates the bag (quarantined) and completes the donation.

    Plain form (not a ModelForm) — it drives DonationService.record_collection,
    which creates the BloodBag; there is no model instance to bind here.
    """

    component = forms.ModelChoiceField(queryset=BloodComponent.objects.filter(is_active=True))
    volume_ml = forms.IntegerField(min_value=1, max_value=1000, initial=450)
    collected_at = forms.DateTimeField(
        required=True,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
    )
    location = forms.CharField(required=False, max_length=120)
    storage_position = forms.CharField(required=False, max_length=60)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.utils import timezone
        if not self.is_bound:
            self.fields["collected_at"].initial = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
