"""System settings administration (ADMIN only).

Every critical settings change is audited. Covers: general organization info,
eligibility rule values, inventory thresholds, blood types/components/test
types and the compatibility rule matrix.
"""
from django.contrib import messages
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.utils import timezone
from django.views import View

from audit import services as audit
from core.forms import StyledModelForm
from core.mixins import AdminRequiredMixin
from core.modals import modal_success
from inventory.models import BloodComponent, BloodType, CompatibilityRule, TestType
from settings_app.models import SystemSetting
from settings_app.services import set_setting


class SettingForm(StyledModelForm):
    class Meta:
        model = SystemSetting
        fields = ["key", "value", "category", "value_type", "description", "is_sensitive"]


class SettingsIndexView(AdminRequiredMixin, View):
    template_name = "settings_app/index.html"

    def get(self, request):
        category = request.GET.get("category", "general")
        settings_qs = SystemSetting.objects.order_by("category", "key")
        categories = SystemSetting.CATEGORY_CHOICES
        return render(request, self.template_name, {
            "category": category,
            "categories": categories,
            "settings": settings_qs.filter(category=category),
            "counts": {c: settings_qs.filter(category=c).count() for c, _ in categories},
        })


class SettingSaveView(AdminRequiredMixin, View):
    def post(self, request, pk=None):
        setting = get_object_or_404(SystemSetting, pk=pk) if pk else None
        form = SettingForm(request.POST, instance=setting)
        if form.is_valid():
            before = setting.value if setting else None
            obj = form.save(commit=False)
            obj.updated_by = request.user
            obj.save()
            audit.log(request, action="SYSTEM_SETTING_CHANGED", module="settings", obj=obj,
                      before={"value": before}, after={"value": obj.value},
                      description=f"Setting '{obj.key}' changed from {before!r} to {obj.value!r}")
            messages.success(request, f"Setting '{obj.key}' saved.")
        else:
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
        return modal_success(request, fallback_redirect=reverse("settings_app:index"))


class SettingDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        setting = get_object_or_404(SystemSetting, pk=pk)
        audit.log(request, action="SYSTEM_SETTING_DELETED", module="settings", obj=setting,
                  description=f"Setting '{setting.key}' (value {setting.value!r}) deleted")
        setting.delete()
        messages.success(request, "Setting removed.")
        return modal_success(request, fallback_redirect=reverse("settings_app:index"))


class QuickSetView(AdminRequiredMixin, View):
    """Inline value update from the settings table (audited)."""

    def post(self, request, pk):
        setting = get_object_or_404(SystemSetting, pk=pk)
        before = setting.value
        setting.value = request.POST.get("value", "")
        setting.updated_by = request.user
        setting.save(update_fields=["value", "updated_by", "updated_at"])
        audit.log(request, action="SYSTEM_SETTING_CHANGED", module="settings", obj=setting,
                  before={"value": before}, after={"value": setting.value},
                  description=f"Setting '{setting.key}' changed from {before!r} to {setting.value!r}")
        messages.success(request, f"{setting.key} updated.")
        return modal_success(request, fallback_redirect=request.META.get("HTTP_REFERER", "/settings/"))


# --- Blood bank configuration ----------------------------------------------------
class BloodTypeForm(StyledModelForm):
    class Meta:
        model = BloodType
        fields = ["abo", "rh", "is_active"]


class ComponentForm(StyledModelForm):
    class Meta:
        model = BloodComponent
        fields = ["name", "code", "default_shelf_life_days", "description", "is_active"]


class TestTypeForm(StyledModelForm):
    class Meta:
        model = TestType
        fields = ["name", "category", "is_required", "is_active"]


class CompatibilityForm(StyledModelForm):
    class Meta:
        model = CompatibilityRule
        fields = ["patient_type", "donor_type", "component", "is_allowed", "note", "is_active"]


class BloodBankConfigView(AdminRequiredMixin, View):
    template_name = "settings_app/blood_bank.html"

    def get(self, request):
        return render(request, self.template_name, {
            "blood_types": BloodType.objects.all(),
            "components": BloodComponent.objects.all(),
            "test_types": TestType.objects.all(),
            "rules": CompatibilityRule.objects.select_related(
                "patient_type", "donor_type", "component", "approved_by").order_by(
                "patient_type__abo", "patient_type__rh", "donor_type__abo", "donor_type__rh"),
            "blood_type_form": BloodTypeForm(),
            "component_form": ComponentForm(),
            "test_type_form": TestTypeForm(),
            "compatibility_form": CompatibilityForm(),
        })


class BloodTypeManageView(AdminRequiredMixin, View):
    def post(self, request, pk=None):
        instance = get_object_or_404(BloodType, pk=pk) if pk else None
        form = BloodTypeForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save()
            audit.log(request, action="BLOOD_TYPE_CONFIGURED", module="settings", obj=obj,
                      description=f"Blood type {obj.code} {'updated' if instance else 'created'}")
            messages.success(request, f"Blood type {obj.code} saved.")
        else:
            messages.error(request, "Could not save blood type (duplicate?).")
        return modal_success(request, fallback_redirect=reverse("settings_app:blood_bank"))


class ComponentManageView(AdminRequiredMixin, View):
    def post(self, request, pk=None):
        instance = get_object_or_404(BloodComponent, pk=pk) if pk else None
        form = ComponentForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save()
            audit.log(request, action="COMPONENT_CONFIGURED", module="settings", obj=obj,
                      description=f"Component '{obj.name}' shelf life {obj.default_shelf_life_days}d saved")
            messages.success(request, f"Component {obj.name} saved.")
        else:
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
        return modal_success(request, fallback_redirect=reverse("settings_app:blood_bank"))


class TestTypeManageView(AdminRequiredMixin, View):
    def post(self, request, pk=None):
        instance = get_object_or_404(TestType, pk=pk) if pk else None
        form = TestTypeForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save()
            audit.log(request, action="TEST_TYPE_CONFIGURED", module="settings", obj=obj,
                      description=f"Test type '{obj.name}' saved (required={obj.is_required})")
            messages.success(request, f"Test type {obj.name} saved.")
        else:
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
        return modal_success(request, fallback_redirect=reverse("settings_app:blood_bank"))


class CompatibilityManageView(AdminRequiredMixin, View):
    """Compatibility rules MUST be approved by an authorized administrator —
    saving records approval (who + when) on each rule."""

    def post(self, request, pk=None):
        instance = get_object_or_404(CompatibilityRule, pk=pk) if pk else None
        form = CompatibilityForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.approved_by = request.user
            obj.approved_at = timezone.now()
            try:
                obj.save()
            except IntegrityError:
                messages.error(request, "That patient/donor/component combination already exists.")
                return modal_success(request, fallback_redirect=reverse("settings_app:blood_bank"))
            audit.log(request, action="COMPATIBILITY_RULE_CONFIGURED", module="settings", obj=obj,
                      description=f"Compatibility rule saved: {obj.patient_type} ← {obj.donor_type} "
                                  f"({'allowed' if obj.is_allowed else 'NOT allowed'})")
            messages.success(request, "Compatibility rule saved and recorded as admin-approved.")
        else:
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
        return modal_success(request, fallback_redirect=reverse("settings_app:blood_bank"))


class CompatibilityDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        rule = get_object_or_404(CompatibilityRule, pk=pk)
        audit.log(request, action="COMPATIBILITY_RULE_DELETED", module="settings", obj=None,
                  object_type="CompatibilityRule", object_id=str(pk),
                  description=f"Compatibility rule removed: {rule.patient_type} ← {rule.donor_type}")
        rule.delete()
        messages.success(request, "Rule removed. Unconfigured pairs are NOT compatible by default.")
        return modal_success(request, fallback_redirect=reverse("settings_app:blood_bank"))
