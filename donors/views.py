"""Donor views: management (staff/admin), self-service (donor), screening."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from audit import services as audit
from core.mixins import AdminRequiredMixin, StaffRequiredMixin, is_htmx
from core.modals import modal_success, render_any
from donors.forms import (
    DonorForm,
    DonorSelfProfileForm,
    DonorStatusForm,
    ScreeningForm,
    question_forms,
)
from donors.models import Donor, DonorScreening, ScreeningQuestion, ScreeningResponse
from donors.services import DonorEligibilityService
from inventory.models import BloodType


def _filter_donors(qs, params):
    q = params.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(donor_code__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
            | Q(middle_name__icontains=q) | Q(contact_number__icontains=q) | Q(email__icontains=q)
        )
    for key, field in (("status", "status"), ("blood_type", "blood_type_id"), ("municipality", "municipality")):
        value = params.get(key)
        if value:
            qs = qs.filter(**{f"{field}__icontains" if field == "municipality" else field: value})
    last_from, last_to = params.get("last_donation_from"), params.get("last_donation_to")
    if last_from or last_to:
        donations_filter = Q(donations__status__in=["COLLECTED", "RELEASED"])
        if last_from:
            donations_filter &= Q(donations__donation_date__gte=last_from)
        if last_to:
            donations_filter &= Q(donations__donation_date__lte=last_to)
        qs = qs.filter(donations_filter).distinct()
    eligibility = params.get("eligibility")
    return qs, q, eligibility


class DonorListView(StaffRequiredMixin, ListView):
    template_name = "donors/list.html"
    context_object_name = "donors"
    paginate_by = 15

    def get_queryset(self):
        qs, self.q, self.eligibility_filter = _filter_donors(
            Donor.objects.select_related("blood_type"), self.request.GET
        )
        self.qs_for_count = qs
        if self.eligibility_filter:
            # Eligibility is computed, so filter in Python over a bounded set.
            ids = [d.pk for d in qs[:500]
                   if DonorEligibilityService.evaluate(d).status == self.eligibility_filter]
            qs = qs.filter(pk__in=ids)
        return qs.order_by("last_name", "first_name")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            "q": self.request.GET.get("q", ""),
            "status_filter": self.request.GET.get("status", ""),
            "blood_type_filter": self.request.GET.get("blood_type", ""),
            "municipality_filter": self.request.GET.get("municipality", ""),
            "eligibility_filter": self.request.GET.get("eligibility", ""),
            "blood_types": BloodType.objects.filter(is_active=True),
            "statuses": Donor.Status.choices,
            "eligibilities": {d.pk: DonorEligibilityService.evaluate(d) for d in ctx["donors"]},
            "municipalities": Donor.objects.exclude(municipality="").values_list(
                "municipality", flat=True).distinct().order_by("municipality")[:100],
        })
        return ctx

    def render_to_response(self, context, **kwargs):
        if is_htmx(self.request):
            return render(self.request, "donors/_table.html", context)
        return super().render_to_response(context, **kwargs)


class DonorCreateView(StaffRequiredMixin, View):
    template_name = "donors/form.html"

    def get(self, request):
        return render_any(request, self.template_name,
                          {"form": DonorForm(), "creating": True}, modal_title="Register Donor")

    def post(self, request):
        form = DonorForm(request.POST)
        if form.is_valid():
            donor = form.save()
            audit.log(request, action="DONOR_CREATED", module="donors", obj=donor,
                      description=f"Donor {donor.donor_code} ({donor.full_name}) registered")
            messages.success(request, f"Donor {donor.donor_code} registered.")
            return modal_success(request, fallback_redirect=reverse("donors:detail", args=[donor.pk]))
        return render_any(request, self.template_name,
                          {"form": form, "creating": True}, modal_title="Register Donor")


class DonorUpdateView(StaffRequiredMixin, View):
    template_name = "donors/form.html"

    def get(self, request, pk):
        donor = get_object_or_404(Donor, pk=pk)
        return render_any(request, self.template_name,
                          {"form": DonorForm(instance=donor), "donor": donor}, modal_title="Edit Donor")

    def post(self, request, pk):
        donor = get_object_or_404(Donor, pk=pk)
        before = {f: getattr(donor, f) for f in
                  ["first_name", "last_name", "contact_number", "email", "status", "blood_type_id"]}
        form = DonorForm(request.POST, instance=donor)
        if form.is_valid():
            form.save()
            after = {f: getattr(donor, f) for f in before}
            changed = {k: (str(before[k]), str(after[k])) for k in before
                       if str(before[k]) != str(after[k])}
            audit.log(request, action="DONOR_UPDATED", module="donors", obj=donor,
                      before={k: v[0] for k, v in changed.items()},
                      after={k: v[1] for k, v in changed.items()},
                      description=f"Donor {donor.donor_code} updated" +
                                  (f": {', '.join(changed)}" if changed else ""))
            messages.success(request, "Donor updated.")
            return modal_success(request, fallback_redirect=reverse("donors:detail", args=[donor.pk]))
        return render_any(request, self.template_name,
                          {"form": form, "donor": donor}, modal_title="Edit Donor")


class DonorDetailView(StaffRequiredMixin, View):
    def get(self, request, pk):
        donor = get_object_or_404(
            Donor.objects.select_related("blood_type", "user"), pk=pk
        )
        return render_any(request, "donors/detail.html", {
            "donor": donor,
            "eligibility": DonorEligibilityService.evaluate(donor),
            "screenings": donor.screenings.select_related("performed_by")[:10],
            "donations": donor.donations.order_by("-donation_date")[:10],
            "appointments": donor.appointments.order_by("-date")[:5],
            "point_transactions": donor.point_transactions.order_by("-created_at")[:10],
            "status_form": DonorStatusForm(instance=donor),
            "modal_maxw": "max-w-5xl",
        }, modal_title=donor.full_name)


class DonorStatusView(StaffRequiredMixin, View):
    def post(self, request, pk):
        donor = get_object_or_404(Donor, pk=pk)
        form = DonorStatusForm(request.POST, instance=donor)
        if form.is_valid():
            before = {"status": donor.status}
            form.save()
            audit.log(request, action="DONOR_STATUS_CHANGED", module="donors", obj=donor,
                      before=before, after={"status": donor.status},
                      description=f"Donor {donor.donor_code} status → {donor.status}"
                                  + (f" ({donor.deferral_reason})" if donor.deferral_reason else ""))
            messages.success(request, "Donor status updated.")
        else:
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
        return modal_success(request, fallback_redirect=reverse("donors:detail", args=[pk]))


class DonorDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        donor = get_object_or_404(Donor, pk=pk)
        donor.soft_delete()
        audit.log(request, action="DONOR_SOFT_DELETED", module="donors", obj=donor,
                  description=f"Donor {donor.donor_code} soft-deleted (history preserved)")
        messages.success(request, f"Donor {donor.donor_code} deactivated (records preserved).")
        return modal_success(request, fallback_redirect=reverse("donors:list"))


# --- Screening ------------------------------------------------------------------
class ScreeningCreateView(StaffRequiredMixin, View):
    template_name = "donors/screening_form.html"

    def _context(self, donor, screening_form, qforms, donation):
        return {"donor": donor, "form": screening_form, "question_forms": qforms,
                "donation": donation,
                "eligibility": DonorEligibilityService.evaluate(donor)}

    def get(self, request, pk):
        donor = get_object_or_404(Donor, pk=pk)
        donation = None
        if request.GET.get("donation"):
            from donations.models import Donation
            donation = get_object_or_404(Donation, pk=request.GET["donation"], donor=donor)
        return render_any(request, self.template_name,
                          self._context(donor, ScreeningForm(), question_forms(), donation),
                          modal_title="New Screening")

    def post(self, request, pk):
        donor = get_object_or_404(Donor, pk=pk)
        donation = None
        if request.POST.get("donation"):
            from donations.models import Donation
            donation = get_object_or_404(Donation, pk=request.POST["donation"], donor=donor)
        form = ScreeningForm(request.POST)
        qforms = question_forms()
        for entry in qforms:
            entry["form"] = type(entry["form"])(request.POST, prefix=entry["form"].prefix)
        form_valid = form.is_valid()
        questions_valid = all(entry["form"].is_valid() for entry in qforms)
        if not (form_valid and questions_valid):
            return render_any(request, self.template_name,
                              self._context(donor, form, qforms, donation), modal_title="New Screening")

        screening = form.save(commit=False)
        screening.donor = donor
        screening.donation = donation
        screening.performed_by = request.user
        screening.save()
        for entry in qforms:
            question = entry["question"]
            ScreeningResponse.objects.create(
                screening=screening, question=question,
                answer_text=entry["form"].cleaned_data.get(f"field_{question.pk}", ""),
            )

        # Reflect the outcome on the donor record (staff decision recorded).
        if screening.result == "DEFERRED" and screening.deferral_days:
            donor.status = Donor.Status.TEMP_DEFERRED
            donor.deferred_until = (screening.performed_at +
                                     timezone.timedelta(days=screening.deferral_days)).date()
            donor.deferral_reason = (screening.notes[:200] or "Deferred at screening")
            donor.save(update_fields=["status", "deferred_until", "deferral_reason", "updated_at"])
        elif screening.result == "CLEARED" and donor.status == Donor.Status.TEMP_DEFERRED:
            if donor.deferred_until and donor.deferred_until <= timezone.localdate():
                donor.status = Donor.Status.ACTIVE
                donor.save(update_fields=["status", "updated_at"])

        # Move the linked donation along its workflow.
        if donation and donation.status in ("REGISTERED", "SCREENING"):
            from donations.services import DonationError, DonationService
            target = {"CLEARED": "APPROVED", "DEFERRED": "DEFERRED", "REJECTED": "REJECTED"}.get(screening.result)
            if target and donation.can_transition_to(target):
                try:
                    DonationService.transition(donation, target, actor=request.user, request=request,
                                               reason=f"Screening result: {screening.result}")
                except DonationError as exc:
                    messages.warning(request, str(exc))
            elif donation.status == "REGISTERED":
                DonationService.transition(donation, "SCREENING", actor=request.user, request=request)

        audit.log(request, action="SCREENING_COMPLETED", module="donors", obj=screening,
                  description=f"Screening for donor {donor.donor_code}: {screening.result}")
        messages.success(request, f"Screening recorded: {screening.get_result_display()}.")
        return modal_success(request, fallback_redirect=reverse("donors:screening_detail", args=[screening.pk]))


class ScreeningDetailView(StaffRequiredMixin, View):
    def get(self, request, pk):
        screening = get_object_or_404(
            DonorScreening.objects.select_related("donor", "performed_by", "donation"), pk=pk
        )
        return render_any(request, "donors/screening_detail.html", {
            "screening": screening,
            "responses": screening.responses.select_related("question"),
        }, modal_title="Screening detail")


# --- Donor self-service -----------------------------------------------------------
class MyDonorProfileView(View):
    def dispatch(self, request, *args, **kwargs):
        if request.user.role not in ("DONOR", "ADMIN", "STAFF"):
            raise PermissionDenied
        donor = getattr(request.user, "donor_profile", None)
        if donor is None:
            raise PermissionDenied("No donor record is linked to this account.")
        # Object-level check: donors only ever see their own record.
        if request.user.role == "DONOR":
            self.donor = donor
        else:
            self.donor = donor
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        donor = self.donor
        from rewards.services import RewardService
        return render(request, "donors/my_profile.html", {
            "donor": donor,
            "eligibility": DonorEligibilityService.evaluate(donor),
            "tier": RewardService.current_tier(donor),
            "next_tier": RewardService.next_tier(donor),
            "donations": donor.donations.order_by("-donation_date")[:10],
            "screenings": donor.screenings.order_by("-performed_at")[:5],
            "appointments": donor.appointments.order_by("-date")[:5],
            "point_transactions": donor.point_transactions.order_by("-created_at")[:10],
        })


class MyDonorProfileEditView(View):
    def dispatch(self, request, *args, **kwargs):
        if request.user.role != "DONOR":
            raise PermissionDenied
        donor = getattr(request.user, "donor_profile", None)
        if donor is None:
            raise PermissionDenied
        self.donor = donor
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render_any(request, "donors/my_profile_edit.html",
                          {"form": DonorSelfProfileForm(instance=self.donor)}, modal_title="Edit profile")

    def post(self, request):
        form = DonorSelfProfileForm(request.POST, instance=self.donor)
        if form.is_valid():
            form.save()
            audit.log(request, action="DONOR_SELF_UPDATED", module="donors", obj=self.donor,
                      description=f"Donor {self.donor.donor_code} updated their own contact information")
            messages.success(request, "Profile updated.")
            return modal_success(request, fallback_redirect=reverse("donors:my_profile"))
        return render_any(request, "donors/my_profile_edit.html", {"form": form}, modal_title="Edit profile")
