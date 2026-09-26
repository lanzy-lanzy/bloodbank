"""Inventory views: dashboard, bag search, testing, guarded transitions, ledger."""
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from audit import services as audit
from core.mixins import StaffRequiredMixin, is_htmx
from core.modals import modal_success, render_any
from inventory.forms import BagRegisterForm, TestResultForm, TransitionForm
from inventory.models import BloodBag, BloodComponent, BloodType, InventoryTransaction, TestResult, TestType
from inventory.services import CompatibilityService, InventoryError, InventoryService

SORT_OPTIONS = {
    "newest": "-collected_at",
    "oldest": "collected_at",
    "expiry": "expires_at",
    "blood_type": "blood_type__abo",
    "status": "status",
}


class InventoryDashboardView(StaffRequiredMixin, View):
    template_name = "inventory/dashboard.html"

    def get(self, request):
        now = timezone.now()
        soon = now + timezone.timedelta(days=7)
        bags = BloodBag.objects.select_related("blood_type", "component")
        matrix = []
        for bt in BloodType.objects.filter(is_active=True).order_by("abo", "rh"):
            base = bags.filter(blood_type=bt)
            matrix.append({
                "blood_type": bt,
                "available": base.filter(status="AVAILABLE", expires_at__gt=soon).count(),
                "reserved": base.filter(status="RESERVED").count(),
                "expiring_soon": base.filter(status="AVAILABLE", expires_at__lte=soon, expires_at__gt=now).count(),
                "quarantined": base.filter(status__in=["QUARANTINED", "TESTING"]).count(),
            })
        return render(request, self.template_name, {
            "total_bags": bags.count(),
            "available": bags.filter(status="AVAILABLE").count(),
            "quarantined": bags.filter(status__in=["QUARANTINED", "TESTING"]).count(),
            "reserved": bags.filter(status="RESERVED").count(),
            "expiring_soon": InventoryService.expiring_soon(),
            "expired": bags.filter(status="EXPIRED").count(),
            "discarded": bags.filter(status="DISCARDED").count(),
            "matrix": matrix,
            "components": BloodComponent.objects.filter(is_active=True),
        })


class BagListView(StaffRequiredMixin, ListView):
    template_name = "inventory/bag_list.html"
    context_object_name = "bags"
    paginate_by = 20

    def get_queryset(self):
        qs = BloodBag.objects.select_related("blood_type", "component", "donor", "donation")
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(bag_code__icontains=q) | Q(donation__donation_code__icontains=q)
                | Q(donor__donor_code__icontains=q) | Q(location__icontains=q)
            )
        for key, field in (("status", "status"), ("blood_type", "blood_type_id"),
                           ("component", "component_id"), ("location", "location")):
            value = self.request.GET.get(key)
            if value:
                qs = qs.filter(**{field: value})
        collected_from = self.request.GET.get("collected_from")
        collected_to = self.request.GET.get("collected_to")
        if collected_from:
            qs = qs.filter(collected_at__date__gte=collected_from)
        if collected_to:
            qs = qs.filter(collected_at__date__lte=collected_to)
        expiry = self.request.GET.get("expiry")
        now = timezone.now()
        if expiry == "expired":
            qs = qs.filter(expires_at__lte=now)
        elif expiry == "soon":
            qs = qs.filter(expires_at__gt=now, expires_at__lte=now + timezone.timedelta(days=7))
        sort = SORT_OPTIONS.get(self.request.GET.get("sort", ""), "-collected_at")
        self.filters = {"q": q, "sort": self.request.GET.get("sort", "")}
        return qs.order_by(sort)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(self.filters)
        ctx.update({
            "statuses": BloodBag.Status.choices,
            "blood_types": BloodType.objects.filter(is_active=True),
            "components": BloodComponent.objects.filter(is_active=True),
            "status_filter": self.request.GET.get("status", ""),
            "blood_type_filter": self.request.GET.get("blood_type", ""),
            "component_filter": self.request.GET.get("component", ""),
            "expiry_filter": self.request.GET.get("expiry", ""),
        })
        return ctx

    def render_to_response(self, context, **kwargs):
        if is_htmx(self.request):
            return render(self.request, "inventory/_bag_table.html", context)
        return super().render_to_response(context, **kwargs)


class BagRegisterView(StaffRequiredMixin, View):
    template_name = "inventory/bag_register.html"

    def get(self, request):
        return render_any(request, self.template_name, {"form": BagRegisterForm()},
                          modal_title="Register External Bag")

    def post(self, request):
        form = BagRegisterForm(request.POST)
        if form.is_valid():
            bag = InventoryService.register_external_bag(
                blood_type=form.cleaned_data["blood_type"],
                component=form.cleaned_data["component"],
                volume_ml=form.cleaned_data["volume_ml"],
                collected_at=form.cleaned_data["collected_at"],
                donor=form.cleaned_data["donor"],
                location=form.cleaned_data["location"],
                storage_position=form.cleaned_data["storage_position"],
                reason=form.cleaned_data["reason"],
                actor=request.user, request=request,
            )
            messages.success(request, f"Bag {bag.bag_code} registered (QUARANTINED).")
            return modal_success(request, fallback_redirect=reverse("inventory:bag_detail", args=[bag.pk]))
        return render_any(request, self.template_name, {"form": form},
                          modal_title="Register External Bag")


class BagDetailView(StaffRequiredMixin, View):
    def get(self, request, pk):
        bag = get_object_or_404(
            BloodBag.objects.select_related("blood_type", "component", "donor", "donation",
                                            "released_by"),
            pk=pk,
        )
        latest_results = {}
        for tt in TestType.objects.filter(is_active=True):
            latest = bag.test_results.filter(test_type=tt).order_by("-performed_at", "-id").first()
            latest_results[tt] = latest
        can_release = bag.can_transition_to("AVAILABLE")
        return render_any(request, "inventory/bag_detail.html", {
            "bag": bag,
            "latest_results": latest_results,
            "test_results": bag.test_results.select_related("test_type", "performed_by", "verified_by")[:20],
            "test_form": TestResultForm(),
            "transition_form": TransitionForm(bag=bag),
            "can_release": can_release,
            "transactions": bag.transactions.select_related("actor")[:50],
            "allocations": bag.allocations.select_related("request", "item")[:10],
            "modal_maxw": "max-w-5xl",
        }, modal_title=f"Bag {bag.bag_code}")


class BagTestResultView(StaffRequiredMixin, View):
    """Record a test result; moves bag QUARANTINED → TESTING on first result."""

    def post(self, request, pk):
        bag = get_object_or_404(BloodBag, pk=pk)
        if bag.status not in ("QUARANTINED", "TESTING"):
            messages.error(request, f"Tests can only be recorded while quarantined/testing (bag is {bag.status}).")
            return modal_success(request, fallback_redirect=reverse("inventory:bag_detail", args=[pk]))
        form = TestResultForm(request.POST)
        if form.is_valid():
            result = form.save(commit=False)
            result.bag = bag
            result.performed_by = request.user
            result.save()
            if bag.status == "QUARANTINED":
                try:
                    InventoryService.transition(bag.pk, "TESTING", actor=request.user, request=request,
                                                reason="First test result recorded")
                except InventoryError as exc:
                    messages.warning(request, str(exc))
            bag.screening_status = (
                BloodBag.ScreeningStatus.IN_PROGRESS
                if result.result_status == TestResult.ResultStatus.PENDING
                else bag.screening_status
            )
            bag.save(update_fields=["screening_status"])
            audit.log(request, action="TEST_RESULT_RECORDED", module="inventory", obj=result,
                      description=f"{bag.bag_code}: {result.test_type.name} → {result.result_status}")
            messages.success(request, "Test result recorded.")
        else:
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
        return modal_success(request, fallback_redirect=reverse("inventory:bag_detail", args=[pk]))


class TestVerifyView(StaffRequiredMixin, View):
    """Verification is a deliberate second-person step recorded on the result."""

    def post(self, request, pk, result_pk):
        bag = get_object_or_404(BloodBag, pk=pk)
        result = get_object_or_404(TestResult, pk=result_pk, bag=bag)
        if result.verified_by:
            messages.info(request, "This result is already verified.")
        else:
            result.verified_by = request.user
            result.verified_at = timezone.now()
            result.save(update_fields=["verified_by", "verified_at"])
            audit.log(request, action="TEST_RESULT_VERIFIED", module="inventory", obj=result,
                      description=f"{bag.bag_code}: {result.test_type.name} verified by {request.user.username}")
            messages.success(request, "Test result verified.")
        return modal_success(request, fallback_redirect=reverse("inventory:bag_detail", args=[pk]))


class BagReleaseView(StaffRequiredMixin, View):
    """Authorized release QUARANTINED/TESTING/RETURNED → AVAILABLE.

    Safety-critical: explicit confirmation required (view refuses without it),
    guards enforced by InventoryService (required tests verified + acceptable).
    """

    def post(self, request, pk):
        bag = get_object_or_404(BloodBag, pk=pk)
        if request.POST.get("confirm") != "RELEASE":
            messages.error(request, "Release was not confirmed.")
            return modal_success(request, fallback_redirect=reverse("inventory:bag_detail", args=[pk]))
        try:
            InventoryService.transition(bag.pk, "AVAILABLE", actor=request.user, request=request,
                                        reason=request.POST.get("reason", "Authorized release"))
            messages.success(request, f"Bag {bag.bag_code} released to AVAILABLE inventory.")
        except InventoryError as exc:
            messages.error(request, str(exc))
        return modal_success(request, fallback_redirect=reverse("inventory:bag_detail", args=[pk]))


class BagTransitionView(StaffRequiredMixin, View):
    """Generic guarded transition (discard, reject, expire, return-to-available, etc.)."""

    def post(self, request, pk):
        bag = get_object_or_404(BloodBag, pk=pk)
        new_status = request.POST.get("new_status", "")
        reason = request.POST.get("reason", "")
        destructive = new_status in ("DISCARDED", "REJECTED")
        if destructive and not reason.strip():
            messages.error(request, "A reason is required to discard or reject a bag.")
            return modal_success(request, fallback_redirect=reverse("inventory:bag_detail", args=[pk]))
        if destructive and request.POST.get("confirm") != "YES":
            messages.error(request, "Action was not confirmed.")
            return modal_success(request, fallback_redirect=reverse("inventory:bag_detail", args=[pk]))
        try:
            InventoryService.transition(bag.pk, new_status, actor=request.user, request=request, reason=reason)
            messages.success(request, f"Bag {bag.bag_code} moved to {new_status.replace('_', ' ').title()}.")
        except InventoryError as exc:
            messages.error(request, str(exc))
        return modal_success(request, fallback_redirect=reverse("inventory:bag_detail", args=[pk]))


class InventoryTransactionListView(StaffRequiredMixin, ListView):
    template_name = "inventory/transactions.html"
    context_object_name = "transactions"
    paginate_by = 30

    def get_queryset(self):
        qs = InventoryTransaction.objects.select_related("bag", "actor", "bag__blood_type")
        q = self.request.GET.get("q", "").strip()
        txn_type = self.request.GET.get("type", "")
        if q:
            qs = qs.filter(Q(bag__bag_code__icontains=q) | Q(reference__icontains=q) | Q(reason__icontains=q))
        if txn_type:
            qs = qs.filter(transaction_type=txn_type)
        self.q, self.txn_type = q, txn_type
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({"q": self.q, "type_filter": self.txn_type,
                    "types": InventoryTransaction.TxnType.choices})
        return ctx


class CompatCheckView(StaffRequiredMixin, View):
    """Compatibility lookup tool: patient type + component → compatible inventory."""

    template_name = "inventory/compat_check.html"

    def get(self, request):
        return render(request, self.template_name, {
            "blood_types": BloodType.objects.filter(is_active=True),
            "components": BloodComponent.objects.filter(is_active=True),
        })

    def post(self, request):
        blood_type = get_object_or_404(BloodType, pk=request.POST.get("blood_type"))
        component = get_object_or_404(BloodComponent, pk=request.POST.get("component"))
        try:
            quantity = max(1, int(request.POST.get("quantity", 1)))
        except ValueError:
            quantity = 1
        bags, shortage = CompatibilityService.find_compatible_inventory(blood_type, component, quantity)
        rules = CompatibilityService.compatible_donor_types(blood_type, component)[1].select_related(
            "donor_type", "approved_by")
        return render(request, self.template_name, {
            "blood_types": BloodType.objects.filter(is_active=True),
            "components": BloodComponent.objects.filter(is_active=True),
            "selected_type": blood_type, "selected_component": component, "quantity": quantity,
            "bags": bags, "shortage": shortage, "rules": rules,
        })
