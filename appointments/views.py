"""Appointment views: staff scheduling + calendar, donor self-booking."""
import calendar as pycalendar
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from appointments.forms import AppointmentForm, AppointmentSelfBookForm
from appointments.models import Appointment
from audit import services as audit
from core.mixins import StaffRequiredMixin, is_htmx
from core.modals import modal_success, render_any

VALID_STATUS_MOVES = {
    "REQUESTED": ["CONFIRMED", "CANCELLED", "NO_SHOW"],
    "CONFIRMED": ["CHECKED_IN", "CANCELLED", "NO_SHOW"],
    "CHECKED_IN": ["COMPLETED", "CANCELLED", "NO_SHOW"],
    "COMPLETED": [], "CANCELLED": [], "NO_SHOW": [],
}


def _parse_date(value, default):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default


class AppointmentListView(StaffRequiredMixin, ListView):
    template_name = "appointments/list.html"
    context_object_name = "appointments"
    paginate_by = 20

    def get_queryset(self):
        qs = Appointment.objects.select_related("donor", "created_by")
        day = _parse_date(self.request.GET.get("date"), timezone.localdate())
        self.day = day
        status = self.request.GET.get("status", "")
        scope = self.request.GET.get("scope", "day")
        self.scope = scope
        if scope == "day":
            qs = qs.filter(date=day)
        elif scope == "week":
            start = day - timedelta(days=day.weekday())
            qs = qs.filter(date__range=[start, start + timedelta(days=6)])
        elif scope == "month":
            qs = qs.filter(date__year=day.year, date__month=day.month)
        if status:
            qs = qs.filter(status=status)
        return qs.order_by("date", "time")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            "day": self.day, "scope": self.scope,
            "status_filter": self.request.GET.get("status", ""),
            "statuses": Appointment.Status.choices,
            "month_grid": _month_grid(self.day) if self.scope == "month" else None,
        })
        return ctx


def _month_grid(day):
    """Calendar month grid with per-day appointment counts."""
    first_weekday = pycalendar.monthrange(day.year, day.month)
    qs = Appointment.objects.filter(date__year=day.year, date__month=day.month)
    counts = {}
    for appt in qs.only("date", "status"):
        counts.setdefault(appt.date, {"total": 0, "open": 0})
        counts[appt.date]["total"] += 1
        if appt.status in Appointment.OPEN_STATUSES:
            counts[appt.date]["open"] += 1
    weeks = []
    week = [None] * first_weekday[0]
    for d in range(1, first_weekday[1] + 1):
        current = date(day.year, day.month, d)
        week.append({"date": current, "counts": counts.get(current), "is_today": current == timezone.localdate()})
        if len(week) == 7:
            weeks.append(week)
            week = []
    if week:
        week += [None] * (7 - len(week))
        weeks.append(week)
    return weeks


class AppointmentCreateView(StaffRequiredMixin, View):
    template_name = "appointments/form.html"

    def get(self, request):
        form = AppointmentForm(initial={"date": request.GET.get("date"), "location": ""})
        return render_any(request, self.template_name, {"form": form, "creating": True},
                          modal_title="Schedule Appointment")

    def post(self, request):
        form = AppointmentForm(request.POST)
        if form.is_valid():
            appointment = form.save(commit=False)
            appointment.created_by = request.user
            appointment.save()
            audit.log(request, action="APPOINTMENT_CREATED", module="appointments", obj=appointment,
                      description=f"Appointment for donor {appointment.donor.donor_code} on "
                                  f"{appointment.date} {appointment.time:%H:%M}")
            messages.success(request, "Appointment scheduled.")
            return modal_success(request, fallback_redirect=reverse("appointments:list"))
        return render_any(request, self.template_name, {"form": form, "creating": True},
                          modal_title="Schedule Appointment")


class AppointmentUpdateView(StaffRequiredMixin, View):
    template_name = "appointments/form.html"

    def get(self, request, pk):
        appointment = get_object_or_404(Appointment, pk=pk)
        return render_any(request, self.template_name,
                          {"form": AppointmentForm(instance=appointment), "appointment": appointment},
                          modal_title="Edit Appointment")

    def post(self, request, pk):
        appointment = get_object_or_404(Appointment, pk=pk)
        form = AppointmentForm(request.POST, instance=appointment)
        if form.is_valid():
            form.save()
            audit.log(request, action="APPOINTMENT_UPDATED", module="appointments", obj=appointment,
                      description=f"Appointment {appointment.pk} updated")
            messages.success(request, "Appointment updated.")
            return modal_success(request, fallback_redirect=reverse("appointments:list"))
        return render_any(request, self.template_name, {"form": form, "appointment": appointment},
                          modal_title="Edit Appointment")


class AppointmentStatusView(StaffRequiredMixin, View):
    """Guarded status move: GET (HTMX) opens a confirmation modal which posts
    back to this URL with the chosen status."""

    confirm_template = "appointments/status_confirm.html"

    def get(self, request, pk):
        appointment = get_object_or_404(Appointment, pk=pk)
        new_status = request.GET.get("to", "")
        if new_status not in VALID_STATUS_MOVES.get(appointment.status, []):
            messages.error(request, f"Cannot change {appointment.status} → {new_status or '?(missing)'}.")
            return redirect("appointments:list")
        return render_any(request, self.confirm_template,
                          {"appointment": appointment, "new_status": new_status},
                          modal_title=f"Mark appointment {new_status.replace('_', '-').lower()}?")

    def post(self, request, pk):
        appointment = get_object_or_404(Appointment, pk=pk)
        new_status = request.POST.get("status", "")
        fallback = request.META.get("HTTP_REFERER") or reverse("appointments:list")
        if new_status not in VALID_STATUS_MOVES.get(appointment.status, []):
            messages.error(request, f"Cannot change {appointment.status} → {new_status}.")
            return modal_success(request, fallback_redirect=fallback)
        old = appointment.status
        appointment.status = new_status
        appointment.save(update_fields=["status"])
        audit.log(request, action=f"APPOINTMENT_{new_status}", module="appointments", obj=appointment,
                  before={"status": old}, after={"status": new_status},
                  description=f"Appointment for {appointment.donor.donor_code}: {old} → {new_status}")
        messages.success(request, f"Appointment marked {new_status.replace('_', ' ').lower()}.")
        return modal_success(request, fallback_redirect=fallback)


# --- Donor self-service ---------------------------------------------------------
class MyAppointmentsView(View):
    def dispatch(self, request, *args, **kwargs):
        donor = getattr(request.user, "donor_profile", None)
        if request.user.role == "DONOR" and donor is None:
            raise PermissionDenied("No donor record is linked to this account.")
        self.donor = donor
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        qs = Appointment.objects.none()
        if self.donor:
            qs = Appointment.objects.filter(donor=self.donor).order_by("-date", "-time")
        return render(request, "appointments/my.html", {
            "appointments": qs[:30],
            "form": AppointmentSelfBookForm(donor=self.donor,
                                            initial={"date": (timezone.localdate() + timedelta(days=1)).isoformat()}),
            "donor": self.donor,
        })

    def post(self, request):
        form = AppointmentSelfBookForm(request.POST, donor=self.donor)
        if form.is_valid():
            appointment = form.save(commit=False)
            appointment.created_by = request.user
            appointment.save()
            audit.log(request, action="APPOINTMENT_REQUESTED", module="appointments", obj=appointment,
                      description=f"Donor {self.donor.donor_code} requested an appointment on {appointment.date}")
            messages.success(request, "Appointment requested — staff will confirm it.")
            return redirect("appointments:my_appointments")
        return render(request, "appointments/my.html", {
            "appointments": Appointment.objects.filter(donor=self.donor).order_by("-date", "-time")[:30],
            "form": form, "donor": self.donor,
        })
