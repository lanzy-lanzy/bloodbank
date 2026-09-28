from django import forms
from django.utils import timezone

from core.forms import StyledModelForm
from requests.models import BloodRequest, Organization, RequestItem


class BloodRequestForm(StyledModelForm):
    """Creates the request header + first item in one form.

    The channel choice is staff-only: a requester account can only ever submit
    for its own organization, while staff may log a walk-in patient who came to
    the counter with no account.
    """

    blood_type = forms.ModelChoiceField(queryset=None)
    component = forms.ModelChoiceField(queryset=None)
    quantity = forms.IntegerField(min_value=1, max_value=500, initial=1)

    class Meta:
        model = BloodRequest
        fields = ["patient_reference", "required_by", "urgency", "clinical_indication",
                  "supporting_document", "channel", "walk_in_contact", "walk_in_phone"]
        widgets = {
            "required_by": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, staff=False, **kwargs):
        super().__init__(*args, **kwargs)
        from inventory.models import BloodComponent, BloodType
        self.staff = staff
        self.fields["blood_type"].queryset = BloodType.objects.filter(is_active=True)
        self.fields["component"].queryset = BloodComponent.objects.filter(is_active=True)
        self.fields["supporting_document"].help_text = "PDF or image, max size per system policy."
        if not staff:
            for name in ("channel", "walk_in_contact", "walk_in_phone"):
                self.fields.pop(name, None)
        else:
            self.fields["channel"].initial = (self.initial.get("channel")
                                              or self.instance.channel
                                              or BloodRequest.Channel.ORGANIZATION)
            self.fields["channel"].help_text = (
                "Walk-in = a patient at the blood bank counter with no requester account. "
                "Direct clinic request: logged by staff, validated at the counter, and booked "
                "against the configured walk-in desk organization.")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("channel") == BloodRequest.Channel.WALK_IN and not (cleaned.get("walk_in_contact") or "").strip():
            self.add_error("walk_in_contact", "Required for a walk-in request: who should the counter deal with?")
        return cleaned

    def save_item(self, blood_request):
        RequestItem.objects.create(
            request=blood_request,
            blood_type=self.cleaned_data["blood_type"],
            component=self.cleaned_data["component"],
            quantity=self.cleaned_data["quantity"],
        )


class RequestItemAddForm(StyledModelForm):
    class Meta:
        model = RequestItem
        fields = ["blood_type", "component", "quantity"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from inventory.models import BloodComponent, BloodType
        self.fields["blood_type"].queryset = BloodType.objects.filter(is_active=True)
        self.fields["component"].queryset = BloodComponent.objects.filter(is_active=True)


class OrganizationForm(StyledModelForm):
    class Meta:
        model = Organization
        fields = ["name", "org_type", "address", "contact_number", "email", "license_number", "is_active"]


class RejectForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=True)


class AllocateForm(forms.Form):
    bag = forms.ModelChoiceField(queryset=None, label="Blood bag")

    def __init__(self, *args, item=None, **kwargs):
        super().__init__(*args, **kwargs)
        from inventory.models import BloodBag
        from inventory.services import CompatibilityService
        self.item = item
        if item:
            allowed_ids, _ = CompatibilityService.compatible_donor_types(item.blood_type, item.component)
            # Expired bags are excluded here too, so the dropdown can never
            # offer a bag the Compatibility Report counts as unavailable.
            self.fields["bag"].queryset = BloodBag.objects.filter(
                status="AVAILABLE", component=item.component, blood_type_id__in=allowed_ids,
                expires_at__gt=timezone.now(),
            ).select_related("blood_type", "component", "donor").order_by("expires_at")
            self.fields["bag"].label_from_instance = lambda b: (
                f"{b.bag_code} · {b.blood_type} {b.component} · exp {b.expires_at:%b %d, %Y}"
            )
