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
        from settings_app.services import get_int_setting
        if days is None:
            days = get_int_setting("expiring_soon_days", 7)
        now = timezone.now()
        return BloodBag.objects.filter(
            status__in=["AVAILABLE", "RESERVED"], expires_at__gt=now,
            expires_at__lte=now + timedelta(days=days),
        ).select_related("blood_type", "component", "donor").order_by("expires_at")


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
