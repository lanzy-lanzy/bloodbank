"""Reporting engine: a registry of reports, each defining its queryset,
columns, filters, permitted roles and CSV export.

CSV exports respect: role permissions, active filters, safe escaping (via the
csv module) and include a generation timestamp row. Sensitive columns are
excluded from exports available to non-admin roles where marked.
"""
import csv
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone


def _donor_rows(qs):
    from donors.services import DonorEligibilityService
    rows = []
    for d in qs.select_related("blood_type"):
        eligibility = DonorEligibilityService.evaluate(d)
        rows.append([d.donor_code, d.full_name, str(d.blood_type or "—"), d.get_status_display(),
                     d.contact_number, d.municipality, eligibility.status,
                     d.last_donation.donation_date.isoformat() if d.last_donation else "—"])
    return rows


REPORTS = {
    "donors": {
        "title": "Donor Master List",
        "group": "Donor Reports",
        "roles": ["ADMIN", "STAFF"],
        "columns": ["Donor ID", "Name", "Blood Type", "Status", "Contact", "Municipality",
                    "Eligibility", "Last Donation"],
        "filters": ["q", "status", "blood_type", "date_from", "date_to"],
        "queryset": lambda params: _donors_qs(params),
        "rows": _donor_rows,
    },
    "donors_by_type": {
        "title": "Donors by Blood Type",
        "group": "Donor Reports",
        "roles": ["ADMIN", "STAFF"],
        "columns": ["Blood Type", "Active Donors", "All Donors"],
        "filters": [],
        "queryset": lambda params: None,
        "rows": lambda qs: _donors_by_type(),
    },
    "eligible_donors": {
        "title": "Eligible Donors",
        "group": "Donor Reports",
        "roles": ["ADMIN", "STAFF"],
        "columns": ["Donor ID", "Name", "Blood Type", "Contact", "Municipality", "Next Eligible"],
        "filters": ["q", "blood_type"],
        "queryset": lambda params: _donors_qs(params),
        "rows": lambda qs: _eligible_donor_rows(qs),
    },
    "deferred_donors": {
        "title": "Deferred Donors",
        "group": "Donor Reports",
        "roles": ["ADMIN", "STAFF"],
        "columns": ["Donor ID", "Name", "Status", "Reason", "Deferred Until"],
        "filters": ["q"],
        "queryset": lambda params: _donors_qs(params, statuses=["TEMP_DEFERRED", "PERM_DEFERRED"]),
        "rows": lambda qs: [[d.donor_code, d.full_name, d.get_status_display(), d.deferral_reason or "—",
                             d.deferred_until.isoformat() if d.deferred_until else "—"]
                            for d in qs.select_related()],
    },
    "donations": {
        "title": "Donations",
        "group": "Donation Reports",
        "roles": ["ADMIN", "STAFF"],
        "columns": ["Donation ID", "Donor", "Date", "Type", "Volume (mL)", "Blood Type", "Status", "Staff"],
        "filters": ["q", "status", "date_from", "date_to"],
        "queryset": lambda params: _donations_qs(params),
        "rows": lambda qs: [[d.donation_code, d.donor.full_name, d.donation_date.isoformat(),
                             d.get_donation_type_display(), d.volume_ml or "—",
                             str(d.blood_type or "—"), d.get_status_display(),
                             d.staff.username if d.staff else "—"]
                            for d in qs.select_related("donor", "blood_type", "staff")],
    },
    "donations_monthly": {
        "title": "Monthly Donation Trend",
        "group": "Donation Reports",
        "roles": ["ADMIN", "STAFF"],
        "columns": ["Month", "Donations", "Total Volume (mL)"],
        "filters": [],
        "queryset": lambda params: None,
        "rows": lambda qs: _donations_monthly(),
    },
    "inventory": {
        "title": "Current Inventory",
        "group": "Inventory Reports",
        "roles": ["ADMIN", "STAFF"],
        "columns": ["Bag ID", "Blood Type", "Component", "Status", "Collected", "Expires", "Volume (mL)", "Location"],
        "filters": ["q", "status", "blood_type", "date_from", "date_to"],
        "queryset": lambda params: _bags_qs(params),
        "rows": lambda qs: [[b.bag_code, str(b.blood_type), str(b.component), b.get_status_display(),
                             timezone.localtime(b.collected_at).strftime("%Y-%m-%d %H:%M"),
                             timezone.localtime(b.expires_at).strftime("%Y-%m-%d %H:%M"),
                             b.volume_ml, b.location or "—"]
                            for b in qs.select_related("blood_type", "component")],
    },
    "expiring": {
        "title": "Expiring Blood (next 7 days)",
        "group": "Inventory Reports",
        "roles": ["ADMIN", "STAFF"],
        "columns": ["Bag ID", "Blood Type", "Component", "Expires", "Days Left", "Status"],
        "filters": [],
        "queryset": lambda params: None,
        "rows": lambda qs: [[b.bag_code, str(b.blood_type), str(b.component),
                             timezone.localtime(b.expires_at).strftime("%Y-%m-%d %H:%M"),
                             b.days_until_expiry, b.get_status_display()]
                            for b in _expiring_qs()],
    },
    "expired_discarded": {
        "title": "Expired & Discarded Blood",
        "group": "Inventory Reports",
        "roles": ["ADMIN", "STAFF"],
        "columns": ["Bag ID", "Blood Type", "Status", "Collected", "Expired/Updated", "Reason (last txn)"],
        "filters": ["date_from", "date_to"],
        "queryset": lambda params: _bags_qs(params, statuses=["EXPIRED", "DISCARDED"]),
        "rows": lambda qs: [[b.bag_code, str(b.blood_type), b.get_status_display(),
                             timezone.localtime(b.collected_at).strftime("%Y-%m-%d"),
                             timezone.localtime(b.expires_at).strftime("%Y-%m-%d"),
                             b.transactions.first().reason if b.transactions.exists() else "—"]
                            for b in qs.select_related("blood_type").prefetch_related("transactions")],
    },
    "inventory_movements": {
        "title": "Inventory Movements (Ledger)",
        "group": "Inventory Reports",
        "roles": ["ADMIN", "STAFF"],
        "columns": ["When", "Bag", "Type", "From", "To", "Actor", "Reason", "Reference"],
        "filters": ["q", "date_from", "date_to"],
        "queryset": lambda params: _txn_qs(params),
        "rows": lambda qs: [[timezone.localtime(t.created_at).strftime("%Y-%m-%d %H:%M"),
                             t.bag.bag_code, t.get_transaction_type_display(), t.previous_status or "—",
                             t.new_status, t.actor.username if t.actor else "system", t.reason or "—",
                             t.reference or "—"]
                            for t in qs.select_related("bag", "actor")],
    },
    "requests": {
        "title": "Blood Requests",
        "group": "Request Reports",
        "roles": ["ADMIN", "STAFF"],
        "columns": ["Request ID", "Organization", "Created", "Urgency", "Status", "Units", "Fulfilled"],
        "filters": ["q", "status", "date_from", "date_to"],
        "queryset": lambda params: _requests_qs(params),
        "rows": lambda qs: [[r.request_code, r.organization.name,
                             timezone.localtime(r.created_at).strftime("%Y-%m-%d"),
                             r.get_urgency_display(), r.get_status_display(),
                             r.total_quantity, r.total_fulfilled]
                            for r in qs.select_related("organization").prefetch_related("items")],
    },
    "emergency_requests": {
        "title": "Emergency Requests",
        "group": "Request Reports",
        "roles": ["ADMIN", "STAFF"],
        "columns": ["Request ID", "Organization", "Urgency", "Required By", "Status", "Units"],
        "filters": ["date_from", "date_to"],
        "queryset": lambda params: _requests_qs(params, urgencies=["EMERGENCY", "CRITICAL"]),
        "rows": lambda qs: [[r.request_code, r.organization.name, r.get_urgency_display(),
                             timezone.localtime(r.required_by).strftime("%Y-%m-%d %H:%M") if r.required_by else "—",
                             r.get_status_display(), r.total_quantity]
                            for r in qs.select_related("organization")],
    },
    "fulfillment_rate": {
        "title": "Fulfillment Rate",
        "group": "Request Reports",
        "roles": ["ADMIN", "STAFF"],
        "columns": ["Metric", "Value"],
        "filters": ["date_from", "date_to"],
        "queryset": lambda params: None,
        "rows": lambda qs: _fulfillment_rate(qs),
    },
    "points": {
        "title": "Points Issued & Redeemed",
        "group": "Reward Reports",
        "roles": ["ADMIN", "STAFF"],
        "columns": ["When", "Donor", "Amount", "Type", "Description", "Balance After", "Actor"],
        "filters": ["q", "date_from", "date_to"],
        "queryset": lambda params: _points_qs(params),
        "rows": lambda qs: [[timezone.localtime(t.created_at).strftime("%Y-%m-%d %H:%M"),
                             t.donor.donor_code, f"{t.amount:+d}", t.get_type_display(),
                             t.description or "—", t.running_balance,
                             t.created_by.username if t.created_by else "system"]
                            for t in qs.select_related("donor", "created_by")],
    },
    "tiers": {
        "title": "Donors by Reward Tier",
        "group": "Reward Reports",
        "roles": ["ADMIN", "STAFF"],
        "columns": ["Tier", "Donors"],
        "filters": [],
        "queryset": lambda params: None,
        "rows": lambda qs: _tier_report(),
    },
    "audit": {
        "title": "Audit Trail",
        "group": "Audit Reports",
        "roles": ["ADMIN"],
        "columns": ["When", "User", "Action", "Module", "Object", "Description"],
        "filters": ["q", "date_from", "date_to"],
        "queryset": lambda params: _audit_qs(params),
        "rows": lambda qs: [[timezone.localtime(a.created_at).strftime("%Y-%m-%d %H:%M:%S"),
                             a.user.username if a.user else "system", a.action, a.module,
                             f"{a.object_type} #{a.object_id}" if a.object_id else "—",
                             a.description or "—"]
                            for a in qs.select_related("user")],
    },
}


# --- queryset builders ------------------------------------------------------------
def _apply_dates(qs, params, field):
    if params.get("date_from"):
        qs = qs.filter(**{f"{field}__gte": params["date_from"]})
    if params.get("date_to"):
        qs = qs.filter(**{f"{field}__lte": params["date_to"]})
    return qs


def _donors_qs(params, statuses=None):
    from donors.models import Donor
    qs = Donor.objects.all()
    if statuses:
        qs = qs.filter(status__in=statuses)
    if params.get("q"):
        qs = qs.filter(Q(donor_code__icontains=params["q"]) | Q(first_name__icontains=params["q"])
                       | Q(last_name__icontains=params["q"]) | Q(contact_number__icontains=params["q"]))
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("blood_type"):
        qs = qs.filter(blood_type_id=params["blood_type"])
    return _apply_dates(qs, params, "created_at")[:1000]


def _eligible_donor_rows(qs):
    from donors.services import DonorEligibilityService
    rows = []
    for d in qs.select_related("blood_type"):
        result = DonorEligibilityService.evaluate(d)
        if result.status == "ELIGIBLE":
            rows.append([d.donor_code, d.full_name, str(d.blood_type or "—"), d.contact_number,
                         d.municipality,
                         result.next_eligible_date.isoformat() if result.next_eligible_date else "Now"])
    return rows


def _donors_by_type():
    from donors.models import Donor
    from inventory.models import BloodType
    rows = []
    for bt in BloodType.objects.filter(is_active=True):
        rows.append([str(bt), Donor.objects.filter(blood_type=bt, status="ACTIVE").count(),
                     Donor.objects.filter(blood_type=bt).count()])
    return rows


def _donations_qs(params, statuses=None):
    from donations.models import Donation
    qs = Donation.objects.all()
    if statuses:
        qs = qs.filter(status__in=statuses)
    if params.get("q"):
        qs = qs.filter(Q(donation_code__icontains=params["q"]) | Q(donor__donor_code__icontains=params["q"]))
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    return _apply_dates(qs, params, "donation_date")[:2000]


def _donations_monthly():
    from django.db.models import Count, Sum
    from django.db.models.functions import TruncMonth
    from donations.models import Donation
    since = timezone.now() - timedelta(days=365)
    rows = (Donation.objects.filter(donation_date__gte=since.date(), status__in=["COLLECTED", "RELEASED"])
            .annotate(month=TruncMonth("donation_date")).values("month")
            .annotate(n=Count("id"), vol=Sum("volume_ml")).order_by("month"))
    return [[r["month"].strftime("%Y-%m"), r["n"], r["vol"] or 0] for r in rows]


def _bags_qs(params, statuses=None):
    from inventory.models import BloodBag
    qs = BloodBag.objects.all()
    if statuses:
        qs = qs.filter(status__in=statuses)
    if params.get("q"):
        qs = qs.filter(bag_code__icontains=params["q"])
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("blood_type"):
        qs = qs.filter(blood_type_id=params["blood_type"])
    return _apply_dates(qs, params, "collected_at")[:2000]


def _expiring_qs():
    from inventory.services import InventoryService
    return InventoryService.expiring_soon()[:500]


def _txn_qs(params):
    from inventory.models import InventoryTransaction
    qs = InventoryTransaction.objects.all()
    if params.get("q"):
        qs = qs.filter(Q(bag__bag_code__icontains=params["q"]) | Q(reference__icontains=params["q"]))
    return _apply_dates(qs, params, "created_at")[:3000]


def _requests_qs(params, urgencies=None):
    from requests.models import BloodRequest
    qs = BloodRequest.objects.all()
    if urgencies:
        qs = qs.filter(urgency__in=urgencies)
    if params.get("q"):
        qs = qs.filter(Q(request_code__icontains=params["q"]) | Q(organization__name__icontains=params["q"]))
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    return _apply_dates(qs, params, "created_at")[:2000]


def _fulfillment_rate(params=None):
    from requests.models import BloodRequest
    qs = BloodRequest.objects.exclude(status__in=["DRAFT", "CANCELLED"])
    if params:
        qs = _apply_dates(qs, params, "created_at")
    total = qs.count()
    fulfilled = qs.filter(status="FULFILLED").count()
    partial = qs.filter(status="PARTIALLY_FULFILLED").count()
    rate = round(fulfilled / total * 100, 1) if total else 0
    return [["Closed requests (non-draft/cancelled)", total], ["Fulfilled", fulfilled],
            ["Partially fulfilled", partial], ["Fulfillment rate (%)", rate]]


def _points_qs(params):
    from rewards.models import PointTransaction
    qs = PointTransaction.objects.all()
    if params.get("q"):
        qs = qs.filter(Q(donor__donor_code__icontains=params["q"]) | Q(description__icontains=params["q"]))
    return _apply_dates(qs, params, "created_at")[:3000]


def _tier_report():
    from donors.models import Donor
    from rewards.services import RewardService
    from rewards.models import RewardTier
    rows = []
    for tier in RewardTier.objects.all():
        count = sum(1 for d in Donor.objects.filter(points_balance__gte=tier.min_points).only("points_balance")
                    if RewardService.current_tier(d) and RewardService.current_tier(d).pk == tier.pk)
        rows.append([tier.name, count])
    return rows


def _audit_qs(params):
    from audit.models import AuditLog
    qs = AuditLog.objects.all()
    if params.get("q"):
        qs = qs.filter(Q(action__icontains=params["q"]) | Q(description__icontains=params["q"])
                       | Q(user__username__icontains=params["q"]))
    return _apply_dates(qs, params, "created_at")[:3000]


def write_csv(response, report_key, rows):
    """Write report rows to an HttpResponse as CSV with timestamp header."""
    report = REPORTS[report_key]
    writer = csv.writer(response)
    writer.writerow([f"{report['title']} — generated {timezone.localtime():%Y-%m-%d %H:%M:%S %Z}"])
    writer.writerow(report["columns"])
    for row in rows:
        writer.writerow(["" if cell is None else cell for cell in row])
    return response
