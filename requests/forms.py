from django import forms

from core.forms import StyledModelForm
from requests.models import BloodRequest, Organization, RequestItem


class BloodRequestForm(StyledModelForm):
    """Creates the request header + first item in one form."""

    blood_type = forms.ModelChoiceField(queryset=None)
    component = forms.ModelChoiceField(queryset=None)
    quantity = forms.IntegerField(min_value=1, max_value=500, initial=1)

    class Meta:
        model = BloodRequest
        fields = ["patient_reference", "required_by", "urgency", "clinical_indication",
                  "supporting_document"]
        widgets = {
            "required_by": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from inventory.models import BloodComponent, BloodType
        self.fields["blood_type"].queryset = BloodType.objects.filter(is_active=True)
        self.fields["component"].queryset = BloodComponent.objects.filter(is_active=True)
        self.fields["supporting_document"].help_text = "PDF or image, max size per system policy."

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
            self.fields["bag"].queryset = BloodBag.objects.filter(
                status="AVAILABLE", component=item.component, blood_type_id__in=allowed_ids,
            ).select_related("blood_type", "component", "donor").order_by("expires_at")
            self.fields["bag"].label_from_instance = lambda b: (
                f"{b.bag_code} · {b.blood_type} {b.component} · exp {b.expires_at:%b %d, %Y}"
            )
