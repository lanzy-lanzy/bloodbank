"""Donor views: management (staff/admin), self-service (donor), screening."""
import csv

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from audit import services as audit
from core.mixins import (AdminRequiredMixin, DonorRequiredMixin, PrintableDocumentMixin,
                         StaffRequiredMixin, is_htmx)
from core.modals import modal_success, render_any
from donors.documents import (DONOR_DIRECTORY_COLUMNS, build_donor_directory_spec,
                              build_interview_sheet_spec, donor_directory_rows,
                              filter_donor_queryset)
from donors.forms import (
    DonorForm,
    DonorInterviewSheetForm,
    DonorSelfProfileForm,
    DonorStatusForm,
    ScreeningForm,
    question_forms,
)
from donors.models import Donor, DonorScreening, ScreeningQuestion, ScreeningResponse
from donors.services import DonorEligibilityService, InterviewSheetService
from inventory.models import BloodType


def _url_without_page(request, url_name: str) -> str:
    """Reverse ``url_name`` preserving active filter params, dropping ``page``."""
    url = reverse(url_name)
    kept = [p for p in request.GET.urlencode().split("&") if p and not p.startswith("page=")]
    return f"{url}?{'&'.join(kept)}" if kept else url


class DonorListView(StaffRequiredMixin, ListView):
    template_name = "donors/list.html"
    context_object_name = "donors"
    paginate_by = 15

    def get_queryset(self):
        return filter_donor_queryset(self.request.GET)

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
            "print_url": _url_without_page(self.request, "donors:directory_document"),
            "pdf_url": _url_without_page(self.request, "donors:directory_pdf"),
            "csv_url": _url_without_page(self.request, "donors:directory_csv"),
            "is_htmx": is_htmx(self.request),
        })
        return ctx

    def render_to_response(self, context, **kwargs):
        if is_htmx(self.request):
            return render(self.request, "donors/_table.html", context)
        return super().render_to_response(context, **kwargs)


class _DonorDirectoryMixin(PrintableDocumentMixin):
    document_url = "donors:directory_document"
    pdf_url = "donors:directory_pdf"
    csv_url = "donors:directory_csv"
    back_url = "donors:list"

    def build_document_spec(self, request, *args, **kwargs):
        return build_donor_directory_spec(request)


class DonorDirectoryDocumentView(StaffRequiredMixin, _DonorDirectoryMixin, View):
    """Print preview of the filtered Donor Registry (same spec as the PDF)."""

    def get(self, request):
        return self.document(request)


class DonorDirectoryPdfView(StaffRequiredMixin, _DonorDirectoryMixin, View):
    """Filtered Donor Registry as a downloadable PDF file."""

    def get(self, request):
        return self.pdf(request)


class DonorDirectoryCsvView(StaffRequiredMixin, View):
    """Filtered Donor Registry as a downloadable UTF-8 CSV file."""

    def get(self, request):
        donors = list(filter_donor_queryset(request.GET))
        rows, _ = donor_directory_rows(donors)
        generated = timezone.localtime()
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="donors_{generated:%Y%m%d_%H%M}.csv"'
        )
        # UTF-8 BOM so Excel opens non-ASCII names cleanly.
        response.write("\ufeff")
        writer = csv.writer(response)
        writer.writerow(DONOR_DIRECTORY_COLUMNS)
        writer.writerows(rows)
        return response


class DonorCreateView(StaffRequiredMixin, View):
    template_name = "donors/form.html"

    def get(self, request):
        return render_any(request, self.template_name,
                          {"form": DonorForm(), "creating": True}, modal_title="Register Donor")

    def post(self, request):
        form = DonorForm(request.POST)
        if form.is_valid():
            donor = form.save()
            # A donor record always comes with its interview sheet, so a
            # counter registration does not start the clinic on a blank form.
            InterviewSheetService.ensure_for_donor(
                donor, actor=request.user, request=request)
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


# --- Donor interview sheet ---------------------------------------------------------
class _InterviewSheetMixin(PrintableDocumentMixin):
    """Shared spec builder for the interview-sheet preview and PDF actions."""

    def get_sheet(self, request, *args, **kwargs):
        raise NotImplementedError

    def build_document_spec(self, request, *args, **kwargs):
        return build_interview_sheet_spec(self.get_sheet(request, *args, **kwargs))


class _StaffSheetMixin(_InterviewSheetMixin):
    document_url = "donors:interview_sheet_document"
    pdf_url = "donors:interview_sheet_pdf"
    back_url = "donors:interview_sheet"

    def get_sheet(self, request, *args, **kwargs):
        donor = get_object_or_404(
            Donor.objects.select_related("blood_type"), pk=kwargs["pk"])
        return InterviewSheetService.ensure_for_donor(
            donor, actor=request.user, request=request)


class _MySheetMixin(_InterviewSheetMixin):
    """Donor self-service: only ever the signed-in donor's own sheet."""

    document_url = "donors:my_interview_sheet_document"
    pdf_url = "donors:my_interview_sheet_pdf"
    back_url = "donors:my_interview_sheet"

    def get_sheet(self, request, *args, **kwargs):
        donor = getattr(request.user, "donor_profile", None)
        if donor is None:
            raise PermissionDenied("No donor record is linked to this account.")
        return InterviewSheetService.ensure_for_donor(
            donor, actor=request.user, request=request)


class InterviewSheetView(StaffRequiredMixin, View):
    """The editable PRC Blood Donor Interview Sheet.

    A full page, not a modal: it is a working surface (37 questionnaire items,
    header fields, the CUE declaration and two signature blocks) and the
    screen it belongs to is the donor's record, which stays visible around it.
    """

    template_name = "donors/interview_sheet.html"

    def _sheet(self, request, pk):
        donor = get_object_or_404(Donor.objects.select_related("blood_type"), pk=pk)
        sheet = InterviewSheetService.ensure_for_donor(
            donor, actor=request.user, request=request)
        return donor, sheet

    def _context(self, donor, sheet, form):
        answered, total, remarks = InterviewSheetService.progress(sheet)
        return {
            "donor": donor,
            "sheet": sheet,
            "form": form,
            "sections": form.sections(),
            "answered": answered,
            "total_items": total,
            "remarks_count": remarks,
        }

    def get(self, request, pk):
        donor, sheet = self._sheet(request, pk)
        form = DonorInterviewSheetForm(
            initial=InterviewSheetService.initial_data(sheet), donor=donor)
        return render(request, self.template_name, self._context(donor, sheet, form))

    def post(self, request, pk):
        donor, sheet = self._sheet(request, pk)
        form = DonorInterviewSheetForm(request.POST, donor=donor)
        if form.is_valid():
            InterviewSheetService.save(sheet, data=form.cleaned_data,
                                       actor=request.user, request=request)
            messages.success(request, "Interview sheet saved.")
            return redirect("donors:interview_sheet", pk=donor.pk)
        messages.error(request, "The interview sheet was not saved - see the errors below.")
        return render(request, self.template_name, self._context(donor, sheet, form))


class InterviewSheetDocumentView(StaffRequiredMixin, _StaffSheetMixin, View):
    """Print preview of the official form (same spec as the PDF)."""

    def get(self, request, pk):
        return self.document(request, pk=pk)


class InterviewSheetPdfView(StaffRequiredMixin, _StaffSheetMixin, View):
    """The interview sheet as a downloadable PDF file."""

    def get(self, request, pk):
        return self.pdf(request, pk=pk)


class MyInterviewSheetView(DonorRequiredMixin, View):
    """The donor's own interview sheet, read-only."""

    template_name = "donors/my_interview_sheet.html"

    def get(self, request):
        donor = getattr(request.user, "donor_profile", None)
        if donor is None:
            raise PermissionDenied("No donor record is linked to this account.")
        sheet = InterviewSheetService.ensure_for_donor(
            donor, actor=request.user, request=request)
        answered, total, remarks = InterviewSheetService.progress(sheet)
        return render(request, self.template_name, {
            "donor": donor,
            "sheet": sheet,
            "groups": InterviewSheetService.section_rows(sheet),
            "answered": answered,
            "total_items": total,
            "remarks_count": remarks,
        })


class MyInterviewSheetDocumentView(DonorRequiredMixin, _MySheetMixin, View):
    def get(self, request):
        return self.document(request)


class MyInterviewSheetPdfView(DonorRequiredMixin, _MySheetMixin, View):
    def get(self, request):
        return self.pdf(request)
