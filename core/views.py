"""Role-based dashboards.

Each role gets a dedicated dashboard. All numbers come from real queries —
no placeholder statistics.
"""
import json
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from core.mixins import is_htmx


def _month_series(qs, date_field, months=6):
    """Donations-per-month series for charts."""
    since = timezone.now() - timedelta(days=months * 31)
    rows = (
        qs.filter(**{f"{date_field}__gte": since})
        .annotate(month=TruncMonth(date_field))
        .values("month")
        .annotate(total=Count("id"))
        .order_by("month")
    )
    return {
        "labels": [r["month"].strftime("%b %Y") for r in rows],
        "data": [r["total"] for r in rows],
    }


@login_required
def dashboard(request):
    role = request.user.role
    if role == "ADMIN":
        return _admin_dashboard(request)
    if role == "STAFF":
        return _staff_dashboard(request)
    if role == "DONOR":
        return _donor_dashboard(request)
    return _requester_dashboard(request)


def home(request):
    """Public landing page at `/`.

    Open to anonymous visitors — no login redirect. All figures shown are
    live aggregate queries (never donor/requester personal data). When an
    inventory count is unavailable the template hides the tile rather than
    showing a made-up number.
    """
    from donors.models import Donor
    from donations.models import Donation
    from inventory.models import BloodBag

    try:
        stats = {
            "donors": Donor.objects.count(),
            "donations": Donation.objects.count(),
            # Only bags explicitly marked AVAILABLE count; "eligible to
            # donate" is a clinical rule and is never summarised here.
            "available_bags": BloodBag.objects.filter(status="AVAILABLE").count(),
        }
    except Exception:  # DB not migrated/seeded yet — tiles hide, page works
        stats = {}
    return render(request, "core/home.html", {"stats": stats})


def _admin_dashboard(request):
    from audit.models import AuditLog
    from donations.models import Donation
    from donors.models import Donor
    from inventory.models import BloodBag
    from notifications.models import Notification
    from requests.models import BloodRequest

    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    soon = now + timedelta(days=7)

    bags = BloodBag.objects.select_related("blood_type", "component")
    requests_qs = BloodRequest.objects.select_related("organization")
    total_donors = Donor.objects.count()
    male_donors = Donor.objects.filter(sex="MALE").count()
    female_donors = Donor.objects.filter(sex="FEMALE").count()
    male_pct = round(male_donors * 100 / total_donors, 1) if total_donors else 0.0
    female_pct = round(female_donors * 100 / total_donors, 1) if total_donors else 0.0

    completed_donations = Donation.objects.filter(status__in=["COLLECTED", "RELEASED"])
    muni_rows = list(
        completed_donations.exclude(donor__municipality="")
        .values("donor__municipality")
        .annotate(total=Count("id"))
        .order_by("-total", "donor__municipality")
    )
    municipality_count = len(muni_rows)
    top_municipality = muni_rows[0]["donor__municipality"] if muni_rows else "—"
    top_municipality_donations = muni_rows[0]["total"] if muni_rows else 0
    total_completed_donations = completed_donations.count()
    avg_donations_per_muni = (
        round(total_completed_donations / municipality_count, 1) if municipality_count else 0.0
    )

    ctx = {
        "total_donors": total_donors,
        "active_donors": Donor.objects.filter(status="ACTIVE").count(),
        "male_donors": male_donors,
        "female_donors": female_donors,
        "male_pct": male_pct,
        "female_pct": female_pct,
        "municipality_count": municipality_count,
        "top_municipality": top_municipality,
        "top_municipality_donations": top_municipality_donations,
        "total_completed_donations": total_completed_donations,
        "avg_donations_per_muni": avg_donations_per_muni,
        "donations_this_month": Donation.objects.filter(created_at__gte=month_start).count(),
        "bags_available": bags.filter(status="AVAILABLE").count(),
        "bags_expiring_soon": bags.filter(status="AVAILABLE", expires_at__lte=soon, expires_at__gt=now).count(),
        "bags_quarantined": bags.filter(status__in=["QUARANTINED", "TESTING"]).count(),
        "pending_requests": requests_qs.filter(status__in=["SUBMITTED", "UNDER_REVIEW", "DRAFT"]).count(),
        "emergency_requests": requests_qs.filter(
            urgency__in=["EMERGENCY", "CRITICAL"]
        ).exclude(status__in=["FULFILLED", "REJECTED", "CANCELLED", "EXPIRED"]).count(),
        "fulfilled_requests": requests_qs.filter(status="FULFILLED").count(),
        "recent_donations": Donation.objects.select_related("donor", "blood_type").order_by("-created_at")[:5],
        "recent_requests": requests_qs.order_by("-created_at")[:5],
        "recent_audit": AuditLog.objects.select_related("user").order_by("-created_at")[:8],
        "recent_notifications": Notification.objects.order_by("-created_at")[:5],
        "low_stock": _low_stock_types(bags),
        "inventory_matrix": _inventory_matrix(bags),
    }
    if not is_htmx(request):
        return render(request, "core/dashboard_admin.html", ctx)
    return render(request, "core/dashboard_admin.html", ctx)


def _staff_dashboard(request):
    from appointments.models import Appointment
    from donations.models import Donation
    from donors.models import DonorScreening
    from inventory.models import BloodBag
    from requests.models import BloodRequest

    now = timezone.now()
    today = now.date()
    soon = now + timedelta(days=7)
    bags = BloodBag.objects.select_related("blood_type", "component")

    ctx = {
        "todays_appointments": Appointment.objects.filter(date=today).exclude(
            status__in=["CANCELLED", "NO_SHOW"]
        ).select_related("donor").order_by("time")[:10],
        "todays_donations": Donation.objects.filter(donation_date=today).select_related("donor").count(),
        "pending_screenings": Donation.objects.filter(status__in=["REGISTERED", "SCREENING"]).count(),
        "pending_tests": BloodBag.objects.filter(
            Q(status="QUARANTINED") | Q(status="TESTING", test_results__result_status="PENDING")
        ).distinct().count(),
        "pending_requests": BloodRequest.objects.filter(status__in=["SUBMITTED", "UNDER_REVIEW"]).count(),
        "emergency_requests": BloodRequest.objects.filter(
            urgency__in=["EMERGENCY", "CRITICAL"]
        ).exclude(status__in=["FULFILLED", "REJECTED", "CANCELLED", "EXPIRED"]).select_related("organization").order_by("-created_at")[:5],
        "expiring_bags": bags.filter(status="AVAILABLE", expires_at__lte=soon, expires_at__gt=now).order_by("expires_at")[:8],
        "low_stock": _low_stock_types(bags),
        "inventory_matrix": _inventory_matrix(bags),
        "recent_activity": Donation.objects.select_related("donor").order_by("-created_at")[:5],
    }
    return render(request, "core/dashboard_staff.html", ctx)


def _donor_dashboard(request):
    from appointments.models import Appointment
    from notifications.models import Notification
    from rewards.services import RewardService

    donor = getattr(request.user, "donor_profile", None)
    if donor is None:
        return render(request, "core/dashboard_donor_no_profile.html")

    from donors.services import DonorEligibilityService

    eligibility = DonorEligibilityService.evaluate(donor)
    ctx = {
        "donor": donor,
        "eligibility": eligibility,
        "total_donations": donor.donations.filter(status="COLLECTED").count() +
                           donor.donations.filter(status="RELEASED").count(),
        "lifetime_volume": donor.donations.aggregate(v=Sum("volume_ml"))["v"] or 0,
        "points_balance": donor.points_balance,
        "current_tier": RewardService.current_tier(donor),
        "upcoming_appointment": Appointment.objects.filter(
            donor=donor, date__gte=timezone.now().date(), status__in=["REQUESTED", "CONFIRMED"]
        ).order_by("date", "time").first(),
        "recent_notifications": Notification.objects.filter(donor=donor, channel="in_app").order_by("-created_at")[:5],
        "recent_donations": donor.donations.order_by("-donation_date")[:5],
    }
    return render(request, "core/dashboard_donor.html", ctx)


def _requester_dashboard(request):
    from requests.models import BloodRequest

    profile = getattr(request.user, "requester_profile", None)
    qs = BloodRequest.objects.none()
    if profile is not None:
        # Blood bank walk-in records are staff-side even when the desk
        # organization matches this requester's organization.
        qs = BloodRequest.objects.filter(organization=profile.organization).exclude(
            channel=BloodRequest.Channel.WALK_IN)
    elif request.user.role in ("ADMIN", "STAFF"):
        qs = BloodRequest.objects.all()

    ctx = {
        "profile": profile,
        "pending": qs.filter(status__in=["DRAFT", "SUBMITTED", "UNDER_REVIEW"]).count(),
        "approved": qs.filter(status__in=["APPROVED", "PARTIALLY_FULFILLED"]).count(),
        "fulfilled": qs.filter(status="FULFILLED").count(),
        "rejected": qs.filter(status="REJECTED").count(),
        "emergency": qs.filter(urgency__in=["EMERGENCY", "CRITICAL"]).exclude(
            status__in=["FULFILLED", "REJECTED", "CANCELLED", "EXPIRED"]).count(),
        "recent_requests": qs.select_related("organization").order_by("-created_at")[:8],
    }
    return render(request, "core/dashboard_requester.html", ctx)


def _inventory_matrix(bags):
    """Rows of {blood_type, available, reserved, expiring_soon} for the dashboard table.

    Same definitions as the inventory dashboard: "available" is in-date stock,
    and "expiring_soon" is the subset of it inside the configurable alert window.
    """
    from inventory.models import BloodType
    from inventory.services import expiring_window_days

    now = timezone.now()
    days = expiring_window_days()
    soon = now + timedelta(days=days)
    rows = []
    for bt in BloodType.objects.filter(is_active=True).order_by("abo", "rh"):
        base = bags.filter(blood_type=bt)
        in_date = base.filter(status="AVAILABLE", expires_at__gt=now)
        rows.append({
            "blood_type": bt,
            "available": in_date.count(),
            "reserved": base.filter(status="RESERVED").count(),
            "expiring_soon": in_date.filter(expires_at__lte=soon).count(),
        })
    return rows


def _low_stock_types(bags):
    """Blood types whose available count is at/below the configured low-stock threshold."""
    from settings_app.services import get_int_setting

    threshold = get_int_setting("low_stock_threshold", 3)
    return [r for r in _inventory_matrix(bags) if r["available"] <= threshold]


# --- Chart data endpoint (Chart.js) ----------------------------------------------
@login_required
def chart_data(request, chart):
    from donations.models import Donation
    from donors.models import Donor
    from inventory.models import BloodBag
    from requests.models import BloodRequest

    if request.user.role not in ("ADMIN", "STAFF"):
        return JsonResponse({}, status=403)

    if chart == "donations_over_time":
        series = _month_series(Donation.objects.filter(status__in=["COLLECTED", "RELEASED"]), "donation_date", 6)
        return JsonResponse(series)
    if chart == "inventory_by_type":
        rows = (
            BloodBag.objects.filter(status="AVAILABLE")
            .values("blood_type__abo", "blood_type__rh")
            .annotate(total=Count("id"))
        )
        labels, data = [], []
        for r in rows:
            labels.append(f"{r['blood_type__abo']}{'+' if r['blood_type__rh'] == 'POS' else '-'}")
            data.append(r["total"])
        return JsonResponse({"labels": labels, "data": data})
    if chart == "component_distribution":
        rows = (
            BloodBag.objects.filter(status__in=["AVAILABLE", "RESERVED"])
            .values("component__name")
            .annotate(total=Count("id"))
        )
        return JsonResponse({"labels": [r["component__name"] for r in rows],
                             "data": [r["total"] for r in rows]})
    if chart == "requests_by_urgency":
        rows = BloodRequest.objects.values("urgency").annotate(total=Count("id"))
        return JsonResponse({"labels": [r["urgency"].title() for r in rows],
                             "data": [r["total"] for r in rows]})
    if chart == "donations_by_municipality":
        rows = (
            Donation.objects.filter(status__in=["COLLECTED", "RELEASED"])
            .values("donor__municipality")
            .annotate(
                total=Count("id"),
                male=Count("id", filter=Q(donor__sex="MALE")),
                female=Count("id", filter=Q(donor__sex="FEMALE")),
            )
            .order_by("-total", "donor__municipality")[:12]
        )
        labels = [(r["donor__municipality"] or "").strip() or "Unspecified" for r in rows]
        return JsonResponse({
            "labels": labels,
            "data": [r["total"] for r in rows],
            "male": [r["male"] for r in rows],
            "female": [r["female"] for r in rows],
        })
    if chart == "donors_by_sex":
        male_donors = Donor.objects.filter(sex="MALE").count()
        female_donors = Donor.objects.filter(sex="FEMALE").count()
        other_donors = Donor.objects.exclude(sex__in=["MALE", "FEMALE"]).count()
        completed = Donation.objects.filter(status__in=["COLLECTED", "RELEASED"])
        male_donations = completed.filter(donor__sex="MALE").count()
        female_donations = completed.filter(donor__sex="FEMALE").count()
        other_donations = completed.exclude(donor__sex__in=["MALE", "FEMALE"]).count()
        labels = ["Male", "Female"]
        donors_data = [male_donors, female_donors]
        donations_data = [male_donations, female_donations]
        if other_donors or other_donations:
            labels.append("Other / Unspecified")
            donors_data.append(other_donors)
            donations_data.append(other_donations)
        return JsonResponse({
            "labels": labels,
            "donors": donors_data,
            "donations": donations_data,
        })
    if chart == "municipality_donation_trend":
        since = timezone.now().date() - timedelta(days=6 * 31)
        completed = Donation.objects.filter(
            status__in=["COLLECTED", "RELEASED"],
            donation_date__gte=since,
        )
        top_munis = list(
            completed.values("donor__municipality")
            .annotate(total=Count("id"))
            .order_by("-total", "donor__municipality")[:5]
        )
        muni_keys = [r["donor__municipality"] for r in top_munis]
        monthly = list(
            completed.filter(donor__municipality__in=muni_keys)
            .annotate(month=TruncMonth("donation_date"))
            .values("month", "donor__municipality")
            .annotate(total=Count("id"))
            .order_by("month")
        )
        months = sorted({r["month"] for r in monthly if r["month"]})
        lookup = {(r["donor__municipality"], r["month"]): r["total"] for r in monthly}
        series = []
        for row in top_munis:
            raw_muni = row["donor__municipality"]
            label = (raw_muni or "").strip() or "Unspecified"
            series.append({
                "label": label,
                "total": row["total"],
                "data": [lookup.get((raw_muni, m), 0) for m in months],
            })
        return JsonResponse({
            "labels": [m.strftime("%b %Y") for m in months],
            "series": series,
        })
    return JsonResponse({}, status=404)


# --- Friendly error pages ----------------------------------------------------------
def _error(request, code, title, message, template_name="errors/error.html"):
    if is_htmx(request):
        # HTMX partial: return a compact error fragment instead of a full page.
        return render(request, "errors/partial.html",
                      {"error_code": code, "error_title": title, "error_message": message},
                      status=code)
    return render(request, template_name,
                  {"error_code": code, "error_title": title, "error_message": message}, status=code)


def handler400(request, exception=None):
    return _error(request, 400, "Bad Request", "The server could not understand this request.")


def handler403(request, exception=None):
    return _error(request, 403, "Access Denied", "You do not have permission to view this page.")


def handler404(request, exception=None):
    return _error(request, 404, "Page Not Found", "The page you are looking for does not exist.")


def handler500(request, exception=None):
    return _error(request, 500, "Server Error", "Something went wrong on our side. The team has been notified.")
