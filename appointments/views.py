"""Appointment views: staff scheduling + calendar, donor self-booking."""
import calendar as pycalendar
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from appointments.forms import AppointmentForm, BroadcastScheduleForm
from appointments.models import Appointment
from appointments.services import notify_confirmation, schedule_all_donors_and_notify
from audit import services as audit
from core.mixins import AdminRequiredMixin, StaffRequiredMixin, is_htmx
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


def _shift_month(day: date, delta: int) -> date:
    """Return the first day of the month offset by ``delta`` (-1 or +1)."""
    month = day.month - 1 + delta
    year = day.year + month // 12
    month = month % 12 + 1
    return date(year, month, 1)


class AppointmentBadgeView(StaffRequiredMixin, View):
    """Sidebar counter badge: open appointments (requested, confirmed, checked-in)."""

    def get(self, request):
        count = Appointment.objects.filter(status__in=Appointment.OPEN_STATUSES).count()
        return render(request, "components/nav_badge.html", {"count": count})


class AppointmentListView(StaffRequiredMixin, ListView):
    template_name = "appointments/list.html"
    context_object_name = "appointments"
    paginate_by = 20

    def get_queryset(self):
        qs = Appointment.objects.select_related("donor", "created_by")
        day = _parse_date(self.request.GET.get("date"), timezone.localdate())
        self.day = day
        status = self.request.GET.get("status", "")
        self.status_filter = status
        scope = self.request.GET.get("scope") or "month"
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
        selected_qs = Appointment.objects.select_related("donor", "created_by").filter(date=self.day)
        if self.status_filter:
            selected_qs = selected_qs.filter(status=self.status_filter)
        selected_appointments = list(selected_qs.order_by("time"))

        requested_dates = list(
            Appointment.objects.filter(status=Appointment.Status.REQUESTED)
            .values("date")
            .annotate(count=Count("id"))
            .order_by("date")[:12]
        )

        ctx.update({
            "day": self.day,
            "today": timezone.localdate(),
            "scope": self.scope,
            "status_filter": self.status_filter,
            "statuses": Appointment.Status.choices,
            "month_grid": _month_grid(self.day, self.status_filter) if self.scope == "month" else None,
            "prev_month": _shift_month(self.day, -1),
            "next_month": _shift_month(self.day, 1),
            "selected_appointments": selected_appointments,
            "requested_dates": requested_dates,
            "requested_total": Appointment.objects.filter(status=Appointment.Status.REQUESTED).count(),
        })
        return ctx

    def render_to_response(self, context, **kwargs):
        if is_htmx(self.request) and self.request.GET.get("panel") == "day":
            return render(self.request, "appointments/_day_panel.html", context)
        return super().render_to_response(context, **kwargs)


def _month_grid(day, status_filter=""):
    """Sunday-first calendar month grid with per-day appointment counts & previews."""
    first_weekday, num_days = pycalendar.monthrange(day.year, day.month)
    # monthrange uses Monday=0..Sunday=6; shift so Sunday=0..Saturday=6.
    start_offset = (first_weekday + 1) % 7
    qs = Appointment.objects.select_related("donor").filter(
        date__year=day.year, date__month=day.month
    )
    if status_filter:
        qs = qs.filter(status=status_filter)
    by_day = {}
    for appt in qs.order_by("time"):
        bucket = by_day.setdefault(appt.date, {
            "total": 0, "open": 0, "requested": 0, "previews": [],
        })
        bucket["total"] += 1
        if appt.status in Appointment.OPEN_STATUSES:
            bucket["open"] += 1
        if appt.status == Appointment.Status.REQUESTED:
            bucket["requested"] += 1
        if len(bucket["previews"]) < 2:
            bucket["previews"].append({
                "time": appt.time,
                "name": appt.donor.first_name or appt.donor.donor_code,
                "status": appt.status,
            })

    today = timezone.localdate()
    weeks = []
    week = [None] * start_offset
    for d in range(1, num_days + 1):
        current = date(day.year, day.month, d)
        week.append({
            "date": current,
            "counts": by_day.get(current),
            "is_today": current == today,
            "is_selected": current == day,
        })
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

    @transaction.atomic
    def post(self, request):
        form = AppointmentForm(request.POST)
        if form.is_valid():
            appointment = form.save(commit=False)
            appointment.created_by = request.user
            appointment.save()
            audit.log(request, action="APPOINTMENT_CREATED", module="appointments", obj=appointment,
                      description=f"Appointment for donor {appointment.donor.donor_code} on "
                                  f"{appointment.date} {appointment.time:%H:%M}")
            notify_confirmation(appointment, request=request)
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

    @transaction.atomic
    def post(self, request, pk):
        appointment = get_object_or_404(Appointment.objects.select_for_update(), pk=pk)
        previous_status = appointment.status
        form = AppointmentForm(request.POST, instance=appointment)
        if form.is_valid():
            form.save()
            audit.log(request, action="APPOINTMENT_UPDATED", module="appointments", obj=appointment,
                      description=f"Appointment {appointment.pk} updated")
            notify_confirmation(appointment, previous_status=previous_status, request=request)
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

    @transaction.atomic
    def post(self, request, pk):
        appointment = get_object_or_404(Appointment.objects.select_for_update(), pk=pk)
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
        notify_confirmation(appointment, previous_status=old, request=request)
        messages.success(request, f"Appointment marked {new_status.replace('_', ' ').lower()}.")
        return modal_success(request, fallback_redirect=fallback)


class AppointmentBroadcastCreateView(AdminRequiredMixin, View):
    """Admin-only: set a required donation schedule for all registered donors and notify them."""

    template_name = "appointments/broadcast_form.html"

    def get(self, request):
        from donors.models import Donor
        form = BroadcastScheduleForm(initial={
            "date": (timezone.localdate() + timedelta(days=1)).isoformat(),
            "time": "09:00",
        })
        return render_any(
            request,
            self.template_name,
            {
                "form": form,
                "active_donor_count": Donor.objects.filter(status=Donor.Status.ACTIVE).count(),
            },
            modal_title="Set Schedule & Notify All Donors",
        )

    def post(self, request):
        from donors.models import Donor
        form = BroadcastScheduleForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            result = schedule_all_donors_and_notify(
                date=cd["date"],
                time=cd["time"],
                location=cd["location"],
                notes=cd.get("notes", ""),
                actor=request.user,
                request=request,
                send_sms=cd.get("send_sms", False),
            )
            msg = (
                f"Schedule set for {cd['date']:%b %d, %Y} at {cd['time']:%I:%M %p}: "
                f"{result.scheduled_count} registered donor(s) scheduled and notified."
            )
            if result.skipped_count:
                msg += f" ({result.skipped_count} skipped due to existing slot.)"
            messages.success(request, msg)
            return modal_success(
                request,
                fallback_redirect=f"{reverse('appointments:list')}?date={cd['date']:%Y-%m-%d}&scope=month",
            )
        return render_any(
            request,
            self.template_name,
            {
                "form": form,
                "active_donor_count": Donor.objects.filter(status=Donor.Status.ACTIVE).count(),
            },
            modal_title="Set Schedule & Notify All Donors",
        )


# --- Donor self-service (read-only view of admin-assigned schedules) -------------
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
            "donor": self.donor,
        })
