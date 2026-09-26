"""Donation views: list/create/detail with lifecycle transitions and collection."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.views import View
from django.views.generic import ListView

from audit import services as audit
from core.mixins import StaffRequiredMixin, is_htmx
from core.modals import modal_success, render_any
from donations.forms import CollectionForm, DonationForm
from donations.models import Donation
from donations.services import DonationError, DonationService


class DonationListView(StaffRequiredMixin, ListView):
    template_name = "donations/list.html"
    context_object_name = "donations"
    paginate_by = 20

    def get_queryset(self):
        qs = Donation.objects.select_related("donor", "blood_type", "staff")
        q = self.request.GET.get("q", "").strip()
        status = self.request.GET.get("status", "")
        date_from = self.request.GET.get("date_from")
        date_to = self.request.GET.get("date_to")
        if q:
            qs = qs.filter(
                Q(donation_code__icontains=q) | Q(donor__donor_code__icontains=q)
                | Q(donor__first_name__icontains=q) | Q(donor__last_name__icontains=q)
            )
        if status:
            qs = qs.filter(status=status)
        if date_from:
            qs = qs.filter(donation_date__gte=date_from)
        if date_to:
            qs = qs.filter(donation_date__lte=date_to)
        self.extra = {"q": q, "status": status, "date_from": date_from, "date_to": date_to}
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(self.extra)
        ctx["statuses"] = Donation.Status.choices
        return ctx

    def render_to_response(self, context, **kwargs):
        if is_htmx(self.request):
            return render(self.request, "donations/_table.html", context)
        return super().render_to_response(context, **kwargs)


class DonationCreateView(StaffRequiredMixin, View):
    template_name = "donations/form.html"

    def get(self, request):
        initial = {}
        donor = None
        if request.GET.get("donor"):
            from donors.models import Donor
            donor = get_object_or_404(Donor, pk=request.GET["donor"])
            initial["donor"] = donor
        if request.GET.get("appointment"):
            from appointments.models import Appointment
            appointment = get_object_or_404(Appointment, pk=request.GET["appointment"])
            initial.update({"donor": appointment.donor, "donation_date": appointment.date,
                            "donation_time": appointment.time, "location": appointment.location})
            initial["_appointment"] = appointment.pk
        form = DonationForm(initial=initial)
        return render_any(request, self.template_name, {"form": form, "creating": True,
                                                        "appointment_id": initial.get("_appointment")},
                          modal_title="Register Donation")

    def post(self, request):
        form = DonationForm(request.POST)
        appointment_id = request.POST.get("appointment")
        if form.is_valid():
            donation = form.save(commit=False)
            donation.staff = request.user
            donation.blood_type = donation.donor.blood_type
            if appointment_id:
                from appointments.models import Appointment
                donation.appointment = get_object_or_404(Appointment, pk=appointment_id, donor=donation.donor)
            donation.save()
            audit.log(request, action="DONATION_CREATED", module="donations", obj=donation,
                      description=f"Donation {donation.donation_code} registered for {donation.donor.donor_code}")
            messages.success(request, f"Donation {donation.donation_code} registered.")
            return modal_success(request, fallback_redirect=reverse("donations:detail", args=[donation.pk]))
        return render_any(request, self.template_name, {"form": form, "creating": True,
                                                        "appointment_id": appointment_id},
                          modal_title="Register Donation")


class DonationDetailView(StaffRequiredMixin, View):
    def get(self, request, pk):
        donation = get_object_or_404(
            Donation.objects.select_related("donor", "blood_type", "staff", "appointment", "screening"), pk=pk
        )
        collection_form = CollectionForm() if donation.status == "APPROVED" else None
        bags = donation.blood_bags.select_related("blood_type", "component")
        return render_any(request, "donations/detail.html", {
            "donation": donation, "collection_form": collection_form, "bags": bags,
            "transitions": donation.TRANSITIONS.get(donation.status, []),
            "transactions": [t for bag in bags for t in bag.transactions.select_related("actor")][:20],
            "modal_maxw": "max-w-5xl",
        }, modal_title=f"Donation {donation.donation_code}")


class DonationTransitionView(StaffRequiredMixin, View):
    def post(self, request, pk):
        donation = get_object_or_404(Donation, pk=pk)
        new_status = request.POST.get("status", "")
        reason = request.POST.get("reason", "")
        fallback = reverse("donations:detail", args=[pk])
        if new_status in ("DEFERRED", "REJECTED", "CANCELLED") and not reason.strip():
            messages.error(request, "A reason is required for deferral/rejection/cancellation.")
            return modal_success(request, fallback_redirect=fallback)
        try:
            DonationService.transition(donation, new_status, actor=request.user, request=request, reason=reason)
            messages.success(request, f"Donation moved to {new_status.replace('_', ' ').lower()}.")
        except DonationError as exc:
            messages.error(request, str(exc))
        return modal_success(request, fallback_redirect=fallback)


class DonationCollectView(StaffRequiredMixin, View):
    def post(self, request, pk):
        donation = get_object_or_404(Donation, pk=pk)
        form = CollectionForm(request.POST)
        fallback = reverse("donations:detail", args=[pk])
        if form.is_valid():
            try:
                _, bag = DonationService.record_collection(
                    donation,
                    component=form.cleaned_data["component"],
                    volume_ml=form.cleaned_data["volume_ml"],
                    collected_at=form.cleaned_data["collected_at"],
                    location=form.cleaned_data["location"],
                    storage_position=form.cleaned_data["storage_position"],
                    actor=request.user, request=request,
                )
                messages.success(request, f"Collection recorded. Bag {bag.bag_code} created (QUARANTINED).")
                return modal_success(request, fallback_redirect=reverse("inventory:bag_detail", args=[bag.pk]),
                                     redirect=reverse("inventory:bag_detail", args=[bag.pk]))
            except DonationError as exc:
                messages.error(request, str(exc))
        else:
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
        return modal_success(request, fallback_redirect=fallback)


# --- Donor self-service ----------------------------------------------------------
class MyDonationHistoryView(View):
    def dispatch(self, request, *args, **kwargs):
        donor = getattr(request.user, "donor_profile", None)
        if request.user.role == "DONOR" and donor is None:
            raise PermissionDenied("No donor record is linked to this account.")
        self.donor = donor
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        donations = Donation.objects.none()
        if self.donor:
            donations = Donation.objects.filter(donor=self.donor).order_by("-donation_date")
        return render(request, "donations/my_history.html",
                      {"donations": donations, "donor": self.donor})
