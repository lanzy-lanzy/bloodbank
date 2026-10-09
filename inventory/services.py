"""Inventory + compatibility services.

All inventory movements go through InventoryService.transition(), which:
  * validates the state machine,
  * applies guards (required tests verified before release),
  * writes the InventoryTransaction ledger row,
  * writes an AuditLog entry,
  * runs inside a DB transaction with row locking (select_for_update) to
    prevent two staff users from allocating the same bag.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from inventory.models import (
    BloodBag,
    CompatibilityRule,
    InventoryTransaction,
    TestResult,
    TestType,
)

logger = logging.getLogger("bloodbank.inventory")

# Maps target statuses to ledger transaction types.
_TXN_FOR_STATUS = {
    "TESTING": InventoryTransaction.TxnType.TESTING,
    "AVAILABLE": InventoryTransaction.TxnType.RELEASE,
    "RESERVED": InventoryTransaction.TxnType.RESERVATION,
    "ISSUED": InventoryTransaction.TxnType.ISSUE,
    "RETURNED": InventoryTransaction.TxnType.RETURN,
    "DISCARDED": InventoryTransaction.TxnType.DISCARD,
    "EXPIRED": InventoryTransaction.TxnType.EXPIRATION,
    "REJECTED": InventoryTransaction.TxnType.ADJUSTMENT,
    "TRANSFUSED": InventoryTransaction.TxnType.ISSUE,
    "QUARANTINED": InventoryTransaction.TxnType.QUARANTINE,
}


class InventoryError(Exception):
    """Raised when a transition violates the state machine or a guard."""


# Statuses in which a bag is still treated as usable stock (and must therefore
# be watched for expiry). Mirrors the expiration rule's own scope.
USABLE_STATUSES = ["QUARANTINED", "TESTING", "AVAILABLE", "RESERVED", "RETURNED"]


def expiring_window_days():
    """The single configurable expiring-soon window, in days.

    Every count, banner and filter that says "expiring soon" reads this, so the
    dashboard, the bag list and the alert window can never disagree.
    """
    from settings_app.services import get_int_setting
    return get_int_setting("expiring_soon_days", 7)


# --- Inventory Statement (printable / PDF) --------------------------------------
STATEMENT_LIST_LIMIT = 5000
"""Cap on bag rows in one printed statement.

A statement is a snapshot for filing, not a data dump. If a filter matches more
rows than this the extras are dropped and the document SAYS so in its notes:
silently truncating a stock count would be the dangerous option.
"""

_STATEMENT_COLUMNS = [
    "Bag Code", "Blood Type", "Component", "Status", "Volume (mL)",
    "Collected", "Expires", "Days Left", "Location", "Donor",
]


def _statement_bags(params):
    """The bag listing a statement prints, under the operator's own filters.

    Mirrors :class:`inventory.views.BagListView` so "what I see on screen" and
    "what the statement lists" are the same set of rows. The limit is applied
    here rather than in the template, and the true total is returned so the
    document can disclose any truncation.
    """
    qs = BloodBag.objects.select_related("blood_type", "component", "donor", "donation")
    if params.get("q"):
        term = params["q"].strip()
        qs = qs.filter(Q(bag_code__icontains=term) | Q(donor__donor_code__icontains=term)
                       | Q(donation__donation_code__icontains=term)
                       | Q(location__icontains=term) | Q(storage_position__icontains=term))
    if params.get("status"):
        qs = qs.filter(status__in=[s for s in params["status"].split(",") if s])
    if params.get("blood_type"):
        qs = qs.filter(blood_type_id=params["blood_type"])
    if params.get("component"):
        qs = qs.filter(component_id=params["component"])
    if params.get("collected_from"):
        qs = qs.filter(collected_at__date__gte=params["collected_from"])
    if params.get("collected_to"):
        qs = qs.filter(collected_at__date__lte=params["collected_to"])

    now = timezone.now()
    expiry = params.get("expiry")
    if expiry == "active":
        qs = qs.filter(expires_at__gt=now)
    elif expiry == "soon":
        qs = qs.filter(expires_at__gt=now, expires_at__lte=now + timedelta(days=expiring_window_days()))
    elif expiry == "expired":
        qs = qs.filter(expires_at__lte=now)

    order = {"oldest": "collected_at", "expiry": "expires_at",
             "blood_type": "blood_type__abo", "status": "status"}.get(
        params.get("sort", ""), "-collected_at")
    qs = qs.order_by(order, "pk")
    return list(qs[:STATEMENT_LIST_LIMIT]), qs.count()


def _statement_rows(bags):
    """Flatten bags to printable cells. Dates are local, not UTC, on paper."""
    rows = []
    for bag in bags:
        rows.append([
            bag.bag_code,
            bag.blood_type.code if bag.blood_type else "-",
            bag.component.code.upper(),
            bag.get_status_display(),
            bag.volume_ml,
            timezone.localtime(bag.collected_at).strftime("%Y-%m-%d"),
            timezone.localtime(bag.expires_at).strftime("%Y-%m-%d"),
            bag.days_until_expiry,
            " / ".join(p for p in (bag.location, bag.storage_position) if p) or "-",
            bag.donor.donor_code if bag.donor else "external",
        ])
    return rows


def build_inventory_statement(request):
    """Assemble the printable Inventory Statement for ``request``.

    Presentation only, and it is built on exactly the queries the dashboard
    uses, so a printed statement can never contradict the screen it was
    generated from. No clinical or operational threshold is decided here: the
    expiry window is read from the configured setting rather than hard-coded, and
    "available" keeps the dashboard's meaning (AVAILABLE *and* in date).
    """
    from core.documents import build_document
    from reports.views import _actor_label

    now = timezone.now()
    days = expiring_window_days()
    bags_qs = BloodBag.objects.select_related("blood_type", "component")
    available = bags_qs.filter(status="AVAILABLE", expires_at__gt=now)
    quarantined = bags_qs.filter(status__in=["QUARANTINED", "TESTING"])
    reserved = bags_qs.filter(status="RESERVED")
    expired = bags_qs.filter(status="EXPIRED")
    discarded = bags_qs.filter(status="DISCARDED")
    expiring = InventoryService.expiring_soon()
    past_expiry_usable = InventoryService.usable_past_expiry()

    bags, matched_total = _statement_bags(request.GET)
    bag_rows = _statement_rows(bags)

    notes = [
        "\"Available\" counts units that are AVAILABLE and still within date; "
        "\"Expiring\" is a subset of it, not a separate pool.",
        f"Expiring-soon window: {days} day(s), read from the configured "
        "expiring_soon_days setting rather than a fixed date.",
        "Stock figures are a point-in-time extract. Bag statuses change only "
        "through audited transitions recorded in the movement ledger.",
    ]
    if matched_total > len(bag_rows):
        notes.append(
            f"LISTING TRUNCATED: {matched_total:,} bag(s) matched the filters but this "
            f"statement lists the first {len(bag_rows):,}. Narrow the filters to produce "
            "a complete statement."
        )
    if past_expiry_usable.exists():
        notes.append(
            f"{past_expiry_usable.count()} bag(s) are past their expiration date but "
            "still sit in a usable status. They are excluded from every usable-stock "
            "figure above until the expiration rule moves them to EXPIRED."
        )

    generated = timezone.localtime()
    user_label = _actor_label(request.user)

    return build_document(
        title="Inventory Statement",
        doc_type="INVENTORY STATEMENT",
        subtitle="Blood stock position, expiry outlook and bag-level listing",
        reference=f"INV-{generated:%Y%m%d-%H%M}",
        columns=_STATEMENT_COLUMNS,
        rows=bag_rows,
        meta=[
            ("Generated on", generated.strftime("%d %b %Y, %H:%M")),
            ("Prepared by", user_label),
            ("Bags listed", f"{len(bag_rows):,}"),
            ("Expiry window", f"{days} day(s)"),
        ],
        summary=[
            ("Total bags", f"{bags_qs.count():,}"),
            ("Available", f"{available.count():,}"),
            ("Quarantine / testing", f"{quarantined.count():,}"),
            ("Reserved", f"{reserved.count():,}"),
            ("Expiring soon", f"{len(expiring):,}"),
            ("Expired", f"{expired.count():,}"),
            ("Discarded", f"{discarded.count():,}"),
        ],
        landscape=True,
        notes=notes,
        prepared_by=user_label,
        prepared_role=f"{str(request.user.role).replace('_', ' ').title()} - Blood Bank",
        empty_text="No blood bags matched the current filters.",
        pdf_filename=f"inventory_statement_{generated:%Y%m%d_%H%M}.pdf",
    )


class InventoryService:
    @staticmethod
    def create_bag_from_donation(donation, *, component, volume_ml, collected_at,
                                 location="", storage_position="", actor=None, request=None):
        """Register a collected bag. Starts QUARANTINED — never auto-available."""
        from audit import services as audit

        expires_at = collected_at + timedelta(days=component.default_shelf_life_days)
        with transaction.atomic():
            bag = BloodBag.objects.create(
                donation=donation,
                donor=donation.donor,
                blood_type=donation.blood_type or donation.donor.blood_type,
                component=component,
                collected_at=collected_at,
                expires_at=expires_at,
                volume_ml=volume_ml,
                location=location,
                storage_position=storage_position,
                status=BloodBag.Status.QUARANTINED,
            )
            InventoryTransaction.objects.create(
                bag=bag,
                transaction_type=InventoryTransaction.TxnType.COLLECTION,
                previous_status="",
                new_status=bag.status,
                actor=actor,
                reason="Blood collection registered",
                reference=donation.donation_code,
            )
            audit.log(request, user=actor, action="BLOOD_BAG_CREATED", module="inventory", obj=bag,
                      description=f"Bag {bag.bag_code} collected ({bag.blood_type}, {bag.component}, {volume_ml} mL)")
        return bag

    @staticmethod
    def register_external_bag(*, blood_type, component, volume_ml, collected_at, donor=None,
                              location="", storage_position="", reason="", actor=None, request=None):
        """Register a bag from an external source (no donation record). Audited."""
        from audit import services as audit

        with transaction.atomic():
            bag = BloodBag.objects.create(
                donation=None, donor=donor, blood_type=blood_type, component=component,
                collected_at=collected_at,
                expires_at=collected_at + timedelta(days=component.default_shelf_life_days),
                volume_ml=volume_ml, location=location, storage_position=storage_position,
                status=BloodBag.Status.QUARANTINED,
            )
            InventoryTransaction.objects.create(
                bag=bag, transaction_type=InventoryTransaction.TxnType.COLLECTION,
                previous_status="", new_status=bag.status, actor=actor,
                reason=reason or "Externally sourced bag registered",
            )
            audit.log(request, user=actor, action="BLOOD_BAG_CREATED", module="inventory", obj=bag,
                      description=f"External bag {bag.bag_code} registered: {reason}")
        return bag

    @staticmethod
    def _guard_release(bag):
        """Release to AVAILABLE requires: every ACTIVE+REQUIRED test type has a
        latest result that is NON_REACTIVE (acceptable) AND verified by staff.
        The application never declares blood medically safe on its own — this
        check only enforces that the institution's configured, human-verified
        test workflow was completed."""
        required = TestType.objects.filter(is_required=True, is_active=True)
        if not required.exists():
            raise InventoryError(
                "No required test types are configured. An authorized administrator must "
                "configure required safety tests before any bag can be released."
            )
        missing, unverified, unacceptable = [], [], []
        for tt in required:
            latest = TestResult.objects.filter(bag=bag, test_type=tt).order_by("-performed_at", "-id").first()
            if latest is None or latest.result_status == TestResult.ResultStatus.PENDING:
                missing.append(tt.name)
            elif latest.result_status != TestResult.ResultStatus.NON_REACTIVE:
                unacceptable.append(f"{tt.name} ({latest.get_result_status_display()})")
            elif latest.verified_by is None:
                unverified.append(tt.name)
        problems = []
        if missing:
            problems.append("missing/pending required tests: " + ", ".join(missing))
        if unverified:
            problems.append("unverified results: " + ", ".join(unverified))
        if unacceptable:
            problems.append("non-acceptable results: " + ", ".join(unacceptable))
        if problems:
            raise InventoryError("Cannot release bag: " + "; ".join(problems))

    @staticmethod
    def transition(bag_id, new_status, *, actor=None, request=None, reason="",
                   reference="", override=False):
        """Move a bag to new_status with locking, guards, ledger and audit."""
        from audit import services as audit

        with transaction.atomic():
            bag = BloodBag.objects.select_for_update().get(pk=bag_id)
            previous = bag.status

            if previous == new_status:
                raise InventoryError(f"Bag is already {previous}.")
            if not bag.can_transition_to(new_status):
                raise InventoryError(
                    f"Illegal transition {previous} → {new_status}. "
                    f"Allowed: {', '.join(bag.TRANSITIONS.get(previous, [])) or 'none (terminal state)'}."
                )
            if new_status in ("AVAILABLE", "RESERVED", "ISSUED") and bag.is_expired_by_date:
                raise InventoryError("Bag is past its expiration date and cannot re-enter usable stock.")
            if new_status == "AVAILABLE" and previous in ("TESTING", "QUARANTINED", "RETURNED"):
                InventoryService._guard_release(bag)

            bag.status = new_status
            if new_status == "AVAILABLE" and previous in ("TESTING", "QUARANTINED", "RETURNED"):
                bag.released_by = actor
                bag.released_at = timezone.now()
                bag.screening_status = BloodBag.ScreeningStatus.COMPLETE
            update_fields = ["status", "released_by", "released_at", "screening_status"]

            txn_type = _TXN_FOR_STATUS.get(new_status, InventoryTransaction.TxnType.ADJUSTMENT)
            if override:
                txn_type = InventoryTransaction.TxnType.ADJUSTMENT

            InventoryTransaction.objects.create(
                bag=bag, transaction_type=txn_type, previous_status=previous,
                new_status=new_status, actor=actor, reason=reason, reference=reference,
            )
            bag.save(update_fields=update_fields)

            action_map = {
                "AVAILABLE": "BLOOD_BAG_RELEASED", "RESERVED": "BLOOD_BAG_RESERVED",
                "ISSUED": "BLOOD_BAG_ISSUED", "DISCARDED": "BLOOD_BAG_DISCARDED",
                "TESTING": "BLOOD_BAG_TESTING", "REJECTED": "BLOOD_BAG_REJECTED",
                "EXPIRED": "BLOOD_BAG_EXPIRED", "RETURNED": "BLOOD_BAG_RETURNED",
                "TRANSFUSED": "BLOOD_BAG_TRANSFUSED", "QUARANTINED": "BLOOD_BAG_QUARANTINED",
            }
            audit.log(
                request, user=actor, action=action_map.get(new_status, "BLOOD_BAG_STATUS_CHANGED"),
                module="inventory", obj=bag,
                before={"status": previous}, after={"status": new_status},
                description=(f"Bag {bag.bag_code}: {previous} → {new_status}"
                             + (f" (OVERRIDE: {reason})" if override else (f" — {reason}" if reason else ""))),
            )
            logger.info("BAG %s %s -> %s by %s", bag.bag_code, previous, new_status, actor)
            return bag

    @staticmethod
    def expire_bags(actor=None, request=None):
        """Move past-expiry usable bags to EXPIRED. Idempotent (safe to re-run)."""
        now = timezone.now()
        expired = []
        bag_ids = BloodBag.objects.filter(
            status__in=["AVAILABLE", "RESERVED", "QUARANTINED", "TESTING", "RETURNED"],
            expires_at__lte=now,
        ).values_list("id", flat=True)
        for bag_id in bag_ids:
            try:
                bag = InventoryService.transition(
                    bag_id, "EXPIRED", actor=actor, request=request,
                    reason="Passed expiration date (automated expiration rule)",
                )
                expired.append(bag)
            except InventoryError as exc:
                logger.warning("expire_bags skip bag %s: %s", bag_id, exc)
        return expired

    @staticmethod
    def expiring_soon(days=None):
        if days is None:
            days = expiring_window_days()
        now = timezone.now()
        return BloodBag.objects.filter(
            status__in=["AVAILABLE", "RESERVED"], expires_at__gt=now,
            expires_at__lte=now + timedelta(days=days),
        ).select_related("blood_type", "component", "donor").order_by("expires_at")

    @staticmethod
    def usable_past_expiry():
        """Bags whose date has passed but whose status still reads as usable.

        The expiration rule is a scheduled command, so between runs these bags
        would otherwise be counted as stock. Every usable-status screen excludes
        them by date; this queryset exists to surface them for the rule.
        """
        return BloodBag.objects.filter(
            status__in=USABLE_STATUSES, expires_at__lte=timezone.now(),
        ).select_related("blood_type", "component").order_by("expires_at")

    @staticmethod
    def attention_count():
        """Stock-side work awaiting staff: expiring-soon stock, bags needing the
        expiration rule, and request items that cannot be filled from stock.

        Read-only dashboard/badge aggregate over demo-scale data; it walks open
        demand lines rather than trying to be a reporting engine.
        """
        from requests.services import BloodRequestService

        now = timezone.now()
        days = expiring_window_days()
        expiring = BloodBag.objects.filter(
            status="AVAILABLE", expires_at__gt=now,
            expires_at__lte=now + timedelta(days=days)).count()
        overdue = InventoryService.usable_past_expiry().count()
        return expiring + overdue + BloodRequestService.shortage_item_count()


class CompatibilityService:
    """Configurable compatibility engine.

    Never uses hard-coded compatibility assumptions: a donor type is only
    considered compatible when an ACTIVE CompatibilityRule row (approved by an
    administrator) explicitly allows it for the patient type (+ component).
    Every decision is traceable via the returned rule references.
    """

    @staticmethod
    def compatible_donor_types(patient_type, component=None):
        rules = CompatibilityRule.objects.filter(
            patient_type=patient_type, is_allowed=True, is_active=True
        ).filter(
            Q(component__isnull=True) | Q(component=component)
        )
        return {r.donor_type_id for r in rules}, rules

    @staticmethod
    def is_compatible(patient_type, donor_type, component=None):
        allowed_ids, rules = CompatibilityService.compatible_donor_types(patient_type, component)
        return donor_type.pk in allowed_ids

    @staticmethod
    def find_compatible_inventory(blood_type, component, quantity):
        """Return (bags, shortage) — AVAILABLE, unexpired bags compatible with the
        requested type/component, ordered by earliest expiry (FIFO), limited to
        what is needed. `shortage` = quantity - len(bags)."""
        allowed_ids, _ = CompatibilityService.compatible_donor_types(blood_type, component)
        if not allowed_ids:
            # No configured rules → nothing may be considered compatible.
            return list(), quantity
        bags = list(
            BloodBag.objects.filter(
                status=BloodBag.Status.AVAILABLE,
                component=component,
                blood_type_id__in=allowed_ids,
                expires_at__gt=timezone.now(),
            ).order_by("expires_at")[:quantity]
        )
        return bags, max(0, quantity - len(bags))

    @staticmethod
    def find_compatible_donors(blood_type, component=None, limit=50):
        """Eligible donors whose own blood type is compatible for the patient —
        used only to build a candidate notification pool. Being listed is NOT a
        medical eligibility determination; donors still require screening."""
        from donors.models import Donor

        allowed_ids, _ = CompatibilityService.compatible_donor_types(blood_type, component)
        if not allowed_ids:
            return Donor.objects.none()
        return (
            Donor.objects.filter(status="ACTIVE", blood_type_id__in=allowed_ids)
            .order_by("-updated_at")[:limit]
        )
