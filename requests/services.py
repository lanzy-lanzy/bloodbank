"""BloodRequestService — request lifecycle, allocation with locking, fulfillment.

Safety-critical operations here run inside transaction.atomic() with
select_for_update() so two staff users can never allocate the same bag.
Every allocation/issue writes both the inventory ledger and the audit log.
"""
import logging

from django.db import transaction
from django.db.models import F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from inventory.services import CompatibilityService, InventoryError, InventoryService
from requests.models import Allocation, BloodRequest, Organization, RequestItem

logger = logging.getLogger("bloodbank.requests")


class RequestError(Exception):
    pass


class BloodRequestService:
    @staticmethod
    def walk_in_organization():
        """The Organization walk-in requests are booked against, or None.

        Walk-in requests still need an owning organization (it is what the
        requester-facing scoping, reports and audit trail key on), so the
        institution designates one — normally the blood bank's own counter —
        via the ``walk_in_organization_id`` setting. Unset or inactive means
        walk-in intake is not configured: callers must fail safe rather than
        pick some organization for a patient.
        """
        from settings_app.services import get_int_setting

        org_id = get_int_setting("walk_in_organization_id", None)
        if not org_id:
            return None
        return Organization.objects.filter(pk=org_id, is_active=True).first()

    @staticmethod
    def submit(blood_request, *, actor=None, request=None):
        from audit import services as audit

        if blood_request.status != BloodRequest.Status.DRAFT:
            raise RequestError("Only DRAFT requests can be submitted.")
        if not blood_request.items.exists():
            raise RequestError("A request needs at least one blood item before submission.")
        blood_request.full_clean()
        if blood_request.is_walk_in:
            # A walk-in never queues for approval, whatever path submits it.
            return BloodRequestService.validate_walk_in(blood_request, actor=actor, request=request)
        with transaction.atomic():
            blood_request.status = BloodRequest.Status.SUBMITTED
            blood_request.save(update_fields=["status", "updated_at"])
            audit.log(request, user=actor, action="REQUEST_SUBMITTED", module="requests", obj=blood_request,
                      description=f"Request {blood_request.request_code} submitted by "
                                  f"{blood_request.organization.name} ({blood_request.urgency})")
            if blood_request.is_emergency:
                BloodRequestService._notify_staff_of_emergency(blood_request)
        return blood_request

    @staticmethod
    def _notify_staff_of_emergency(blood_request):
        """In-app alerts to staff/admin users for emergency requests."""
        from accounts.models import User
        from notifications.services import NotificationService

        for user in User.objects.filter(role__in=["ADMIN", "STAFF"], is_active=True):
            try:
                NotificationService.send_to_user(
                    user, "emergency_request_alert",
                    {
                        "request_id": blood_request.request_code,
                        "organization": blood_request.organization.name,
                        "urgency": blood_request.get_urgency_display(),
                    },
                    channels=["in_app"],
                )
            except Exception:  # noqa: BLE001 — alerting must not break submission
                logger.exception("Emergency staff alert failed for user %s", user.username)

    @staticmethod
    def start_review(blood_request, *, actor=None, request=None):
        from audit import services as audit

        if blood_request.status not in (BloodRequest.Status.SUBMITTED,):
            raise RequestError("Only SUBMITTED requests can enter review.")
        with transaction.atomic():
            blood_request.status = BloodRequest.Status.UNDER_REVIEW
            blood_request.assigned_staff = actor
            blood_request.save(update_fields=["status", "assigned_staff", "updated_at"])
            audit.log(request, user=actor, action="REQUEST_UNDER_REVIEW", module="requests", obj=blood_request,
                      description=f"Review started for {blood_request.request_code}")
        return blood_request

    @staticmethod
    def compatibility_report(blood_request):
        """For each item: compatible available bags + shortage. Traceable —
        every result names the bags considered."""
        report = []
        for item in blood_request.items.select_related("blood_type", "component"):
            bags, shortage = CompatibilityService.find_compatible_inventory(
                item.blood_type, item.component, item.outstanding
            )
            report.append({
                "item": item,
                "bags": bags,
                "shortage": shortage,
                "rules_configured": CompatibilityService.compatible_donor_types(
                    item.blood_type, item.component)[1].exists(),
            })
        return report

    @staticmethod
    def open_demand():
        """Demand lines the blood bank still owes: approved / partially fulfilled
        requests with units outstanding, each matched against compatible stock.

        Read-only aggregate for the inventory dashboard and its badge. Stock
        comes only from CompatibilityService against the configured rule set, so
        an unapproved rule set reports a shortage rather than inventing supply.
        """
        rows = []
        items = (RequestItem.objects
                 .filter(request__status__in=[BloodRequest.Status.APPROVED,
                                              BloodRequest.Status.PARTIALLY_FULFILLED])
                 .select_related("request__organization", "blood_type", "component")
                 .order_by("request__required_by", "request__pk", "pk"))
        for item in items:
            if item.outstanding <= 0:
                continue
            # A reserved bag has already left the AVAILABLE pool, so free stock is
            # measured against the units still open to reserve — otherwise an
            # item half-covered by reservations would read as a hard shortage.
            reservable = item.reservable_units
            bags, _ = CompatibilityService.find_compatible_inventory(
                item.blood_type, item.component, reservable)
            rows.append({
                "request": item.request,
                "item": item,
                "outstanding": item.outstanding,
                "in_flight": item.units_reserved,
                "reservable": reservable,
                "compatible": len(bags),
                "shortage": max(0, reservable - len(bags)),
                "rules_configured": CompatibilityService.compatible_donor_types(
                    item.blood_type, item.component)[1].exists(),
            })
        return rows

    @staticmethod
    def shortage_item_count():
        """Open demand lines that cannot be filled from stock on hand."""
        return sum(1 for row in BloodRequestService.open_demand() if row["shortage"])

    @staticmethod
    def validate_walk_in(blood_request, *, actor=None, request=None):
        """A walk-in is a direct clinic request: the staff member at the counter
        is the authority, so it never enters the approval queue.

        This replaces a form, not a guard — bags still only reserve against
        APPROVED status and issuing stays a separate authorized step, so walk-in
        and organization requests keep identical inventory guarantees.
        """
        from audit import services as audit

        if not blood_request.is_walk_in:
            raise RequestError("Only walk-in requests are validated at the counter.")
        if blood_request.status not in (BloodRequest.Status.DRAFT, BloodRequest.Status.SUBMITTED):
            raise RequestError(f"Cannot validate a walk-in in status {blood_request.status}.")
        if not blood_request.items.exists():
            raise RequestError("A request needs at least one blood item before it can be validated.")
        with transaction.atomic():
            blood_request.status = BloodRequest.Status.APPROVED
            blood_request.approved_by = actor
            blood_request.approved_at = timezone.now()
            blood_request.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
            audit.log(request, user=actor, action="REQUEST_VALIDATED_AT_COUNTER", module="requests",
                      obj=blood_request,
                      description=f"Walk-in {blood_request.request_code} validated at the counter by "
                                  f"{getattr(actor, 'username', 'staff')} "
                                  f"(with {blood_request.walk_in_contact})")
            if blood_request.is_emergency:
                # Nobody outside the counter saw the submission, so the alert
                # still has to go out.
                BloodRequestService._notify_staff_of_emergency(blood_request)
        return blood_request

    @staticmethod
    def approve(blood_request, *, actor=None, request=None):
        """Move SUBMITTED/UNDER_REVIEW → APPROVED. Does NOT allocate bags —
        allocation and issue are separate authorized steps. Walk-ins never see
        this: see validate_walk_in()."""
        from audit import services as audit

        if blood_request.is_walk_in:
            raise RequestError("Walk-in requests are validated at the counter, not approved.")
        if blood_request.status not in (BloodRequest.Status.SUBMITTED, BloodRequest.Status.UNDER_REVIEW):
            raise RequestError(f"Cannot approve a request in status {blood_request.status}.")
        with transaction.atomic():
            blood_request.status = BloodRequest.Status.APPROVED
            blood_request.approved_by = actor
            blood_request.approved_at = timezone.now()
            blood_request.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
            audit.log(request, user=actor, action="REQUEST_APPROVED", module="requests", obj=blood_request,
                      description=f"Request {blood_request.request_code} approved")
            BloodRequestService._notify_requester(blood_request, "request_approved")
        return blood_request

    @staticmethod
    def reject(blood_request, *, reason, actor=None, request=None):
        from audit import services as audit

        if blood_request.is_walk_in:
            raise RequestError("Walk-in requests are not approved or rejected — cancel one "
                               "the counter cannot proceed with.")
        if blood_request.status in (BloodRequest.Status.FULFILLED, BloodRequest.Status.REJECTED,
                                    BloodRequest.Status.CANCELLED):
            raise RequestError(f"Cannot reject a request in status {blood_request.status}.")
        if not reason.strip():
            raise RequestError("A rejection reason is required.")
        # Release any active reservations first.
        for allocation in blood_request.allocations.filter(status=Allocation.Status.RESERVED):
            BloodRequestService.cancel_allocation(allocation, actor=actor, request=request,
                                                  reason="Request rejected")
        with transaction.atomic():
            blood_request.status = BloodRequest.Status.REJECTED
            blood_request.rejection_reason = reason
            blood_request.save(update_fields=["status", "rejection_reason", "updated_at"])
            audit.log(request, user=actor, action="REQUEST_REJECTED", module="requests", obj=blood_request,
                      description=f"Request {blood_request.request_code} rejected: {reason}")
            BloodRequestService._notify_requester(
                blood_request, "request_rejected", {"rejection_reason": reason.strip()})
        return blood_request

    @staticmethod
    def cancel(blood_request, *, reason, actor=None, request=None):
        from audit import services as audit

        if blood_request.status in (BloodRequest.Status.FULFILLED, BloodRequest.Status.CANCELLED):
            raise RequestError(f"Cannot cancel a request in status {blood_request.status}.")
        for allocation in blood_request.allocations.filter(status=Allocation.Status.RESERVED):
            BloodRequestService.cancel_allocation(allocation, actor=actor, request=request,
                                                  reason="Request cancelled")
        with transaction.atomic():
            blood_request.status = BloodRequest.Status.CANCELLED
            blood_request.cancellation_reason = reason
            blood_request.save(update_fields=["status", "cancellation_reason", "updated_at"])
            audit.log(request, user=actor, action="REQUEST_CANCELLED", module="requests", obj=blood_request,
                      description=f"Request {blood_request.request_code} cancelled: {reason}")
        return blood_request

    # --- allocation / fulfillment ------------------------------------------------
    @staticmethod
    def allocate_bag(item, bag, *, actor=None, request=None):
        """Reserve one AVAILABLE bag for a request item. Row-locked; refuses
        incompatible, expired or already-allocated bags."""
        from audit import services as audit

        if item.request.status not in (BloodRequest.Status.APPROVED, BloodRequest.Status.PARTIALLY_FULFILLED):
            raise RequestError("Bags can only be allocated to APPROVED requests.")
        if item.outstanding <= 0:
            raise RequestError("This item is already fully allocated.")
        if item.reservable_units <= 0:
            raise RequestError(
                f"All {item.quantity} requested unit(s) are already reserved or issued "
                f"for this item ({item.units_reserved} still reserved).")

        with transaction.atomic():
            # Re-read the item under a row lock: outstanding ignores in-flight
            # reservations, so without this two staff could each reserve stock
            # for the same unit.
            locked_item = RequestItem.objects.select_for_update().get(pk=item.pk)
            if locked_item.reservable_units <= 0:
                raise RequestError(
                    f"All {locked_item.quantity} requested unit(s) are already reserved or issued "
                    f"for this item ({locked_item.units_reserved} still reserved).")
            locked_bag = type(bag).objects.select_for_update().get(pk=bag.pk)
            if locked_bag.status != "AVAILABLE":
                raise RequestError(f"Bag {locked_bag.bag_code} is {locked_bag.status}, not AVAILABLE.")
            if not CompatibilityService.is_compatible(item.blood_type, locked_bag.blood_type, item.component):
                raise RequestError(
                    f"Bag {locked_bag.bag_code} ({locked_bag.blood_type}) is not compatible with "
                    f"requested {item.blood_type} per configured compatibility rules."
                )
            if locked_bag.component_id != item.component_id:
                raise RequestError("Bag component does not match the requested component.")
            try:
                InventoryService.transition(
                    locked_bag.pk, "RESERVED", actor=actor, request=request,
                    reason=f"Reserved for request {item.request.request_code}",
                    reference=item.request.request_code,
                )
            except InventoryError as exc:
                raise RequestError(str(exc)) from exc
            allocation = Allocation.objects.create(
                request=item.request, item=item, bag=locked_bag,
                status=Allocation.Status.RESERVED, allocated_by=actor,
            )
            audit.log(request, user=actor, action="BLOOD_BAG_ALLOCATED", module="requests", obj=allocation,
                      description=f"Bag {locked_bag.bag_code} reserved for {item.request.request_code} "
                                  f"(item {item.blood_type} {item.component})")
        return allocation

    @staticmethod
    def cancel_allocation(allocation, *, actor=None, request=None, reason=""):
        from audit import services as audit

        if allocation.status != Allocation.Status.RESERVED:
            raise RequestError("Only RESERVED allocations can be cancelled.")
        with transaction.atomic():
            InventoryService.transition(
                allocation.bag_id, "AVAILABLE", actor=actor, request=request,
                reason=f"Allocation cancelled: {reason}" if reason else "Allocation cancelled",
                reference=allocation.request.request_code,
            )
            allocation.status = Allocation.Status.CANCELLED
            allocation.save(update_fields=["status"])
            audit.log(request, user=actor, action="ALLOCATION_CANCELLED", module="requests", obj=allocation,
                      description=f"Allocation of {allocation.bag.bag_code} cancelled. {reason}")
        return allocation

    @staticmethod
    def issue_allocation(allocation, *, actor=None, request=None):
        """Final release to the requesting organization: RESERVED → ISSUED.
        This is a safety-critical step requiring authorized staff confirmation
        (enforced in the view layer via confirmation + permission)."""
        from audit import services as audit

        if allocation.status != Allocation.Status.RESERVED:
            raise RequestError("Only RESERVED allocations can be issued.")
        with transaction.atomic():
            InventoryService.transition(
                allocation.bag_id, "ISSUED", actor=actor, request=request,
                reason=f"Issued against request {allocation.request.request_code}",
                reference=allocation.request.request_code,
            )
            allocation.status = Allocation.Status.ISSUED
            allocation.issued_by = actor
            allocation.issued_at = timezone.now()
            allocation.save(update_fields=["status", "issued_by", "issued_at"])

            item = allocation.item
            item.fulfilled_quantity += 1
            item.save(update_fields=["fulfilled_quantity"])

            blood_request = allocation.request
            BloodRequestService._refresh_fulfillment_status(blood_request, actor=actor, request=request)
            audit.log(request, user=actor, action="BLOOD_BAG_ISSUED", module="requests", obj=allocation,
                      description=f"Bag {allocation.bag.bag_code} issued for {blood_request.request_code}")
        return allocation

    @staticmethod
    def mark_transfused(allocation, *, actor=None, request=None):
        """Record that issued blood was transfused (feedback from the requester,
        or from staff for a walk-in record, which has no requester account)."""
        if allocation.status != Allocation.Status.ISSUED:
            raise RequestError("Only ISSUED allocations can be marked transfused.")
        InventoryService.transition(allocation.bag_id, "TRANSFUSED", actor=actor, request=request,
                                    reason=f"Transfusion recorded for {allocation.request.request_code}")
        return allocation

    @staticmethod
    def return_allocation(allocation, *, actor=None, request=None, reason=""):
        """Blood returned unused: ISSUED → RETURNED allocation; bag goes back to
        RETURNED status (must then be re-released through the normal workflow)."""
        from audit import services as audit

        if allocation.status != Allocation.Status.ISSUED:
            raise RequestError("Only ISSUED allocations can be returned.")
        with transaction.atomic():
            InventoryService.transition(allocation.bag_id, "RETURNED", actor=actor, request=request,
                                        reason=reason or "Returned by requester")
            allocation.status = Allocation.Status.RETURNED
            allocation.save(update_fields=["status"])
            item = allocation.item
            item.fulfilled_quantity = max(0, item.fulfilled_quantity - 1)
            item.save(update_fields=["fulfilled_quantity"])
            BloodRequestService._refresh_fulfillment_status(allocation.request, actor=actor, request=request)
            audit.log(request, user=actor, action="ALLOCATION_RETURNED", module="requests", obj=allocation,
                      description=f"Bag {allocation.bag.bag_code} returned for {allocation.request.request_code}")
        return allocation

    @staticmethod
    def _refresh_fulfillment_status(blood_request, *, actor=None, request=None):
        from audit import services as audit

        blood_request.refresh_from_db()
        total = blood_request.total_quantity
        fulfilled = blood_request.total_fulfilled
        if fulfilled == 0:
            return
        new_status = (BloodRequest.Status.FULFILLED if fulfilled >= total
                      else BloodRequest.Status.PARTIALLY_FULFILLED)
        if blood_request.status != new_status:
            old = blood_request.status
            blood_request.status = new_status
            blood_request.save(update_fields=["status", "updated_at"])
            audit.log(request, user=actor, action="REQUEST_FULFILLED" if new_status == "FULFILLED"
                      else "REQUEST_PARTIALLY_FULFILLED", module="requests", obj=blood_request,
                      before={"status": old}, after={"status": new_status},
                      description=f"{blood_request.request_code}: {fulfilled}/{total} units fulfilled")
            if new_status == BloodRequest.Status.FULFILLED:
                BloodRequestService._notify_requester(blood_request, "request_fulfilled")

    @staticmethod
    def _notify_requester(blood_request, template_code, extra=None):
        from notifications.services import NotificationService

        if blood_request.is_walk_in:
            return  # no requester account behind a counter record — staff work it in-page
        if blood_request.created_by:
            try:
                context = {
                    "request_id": blood_request.request_code,
                    "organization": blood_request.organization.name,
                }
                context.update(extra or {})
                NotificationService.send_to_user(
                    blood_request.created_by, template_code, context,
                    channels=["in_app"],
                )
            except Exception:  # noqa: BLE001
                logger.exception("Requester notification %s failed", template_code)

    @staticmethod
    def expire_overdue(actor=None, request=None):
        """Requests whose required_by has passed while still open → EXPIRED."""
        from audit import services as audit

        now = timezone.now()
        expired = []
        for br in BloodRequest.objects.filter(
            status__in=[BloodRequest.Status.SUBMITTED, BloodRequest.Status.UNDER_REVIEW],
            required_by__lt=now,
        ):
            br.status = BloodRequest.Status.EXPIRED
            br.save(update_fields=["status", "updated_at"])
            audit.log(request, user=actor, action="REQUEST_EXPIRED", module="requests", obj=br,
                      description=f"{br.request_code} expired (required-by date passed unfulfilled)")
            expired.append(br)
        return expired

    # --- reporting helpers -------------------------------------------------------
    @staticmethod
    def _requests_owing_units():
        """PKs of APPROVED/PARTIALLY_FULFILLED requests that still owe units.

        Counted in SQL because total_quantity/total_fulfilled are Python
        properties, so the ORM cannot filter on them directly.
        """
        return (
            BloodRequest.objects.order_by()
            .annotate(
                units_requested=Coalesce(Sum("items__quantity"), Value(0)),
                units_fulfilled=Coalesce(Sum("items__fulfilled_quantity"), Value(0)),
            )
            .filter(
                status__in=[BloodRequest.Status.APPROVED,
                            BloodRequest.Status.PARTIALLY_FULFILLED],
                units_requested__gt=F("units_fulfilled"),
            ).values("pk")
        )

    @staticmethod
    def walk_in_queue():
        """Counter records, newest demand first — the Walk-in Desk queue."""
        return (BloodRequest.objects
                .filter(channel=BloodRequest.Channel.WALK_IN)
                .select_related("organization")
                .prefetch_related("items__blood_type", "items__component")
                .order_by(F("required_by").asc(nulls_last=True), "-pk"))

    @staticmethod
    def walk_in_action_count():
        """Walk-ins the counter staff still have to act on: unvalidated drafts
        plus approved ones owing bags. Nothing waiting on an approval decision —
        a walk-in never enters that queue (D-019)."""
        return (BloodRequest.objects
                .filter(channel=BloodRequest.Channel.WALK_IN)
                .filter(Q(status=BloodRequest.Status.DRAFT)
                        | Q(pk__in=BloodRequestService._requests_owing_units()))
                .count())

    @staticmethod
    def awaiting_action_count(user):
        """How many requests are waiting on this user's side of the desk.

        Drives the sidebar badge. Staff/admin see requests still needing a
        decision (SUBMITTED / UNDER_REVIEW) plus approved ones that still owe
        units; a requester sees their own organization's open requests.
        Counted in SQL because total_quantity/total_fulfilled are Python
        properties, so the ORM cannot filter on them directly.
        """
        if user.role in ("ADMIN", "STAFF"):
            return BloodRequest.objects.filter(
                Q(status__in=[BloodRequest.Status.SUBMITTED,
                              BloodRequest.Status.UNDER_REVIEW])
                | Q(pk__in=BloodRequestService._requests_owing_units())).count()
        if user.role == "REQUESTER":
            profile = getattr(user, "requester_profile", None)
            if profile is None:
                return 0
            return BloodRequest.objects.filter(
                organization=profile.organization,
                status__in=BloodRequest.OPEN_STATUSES).exclude(
                channel=BloodRequest.Channel.WALK_IN).count()
        return 0

    # --- emergency workflow --------------------------------------------------------
    @staticmethod
    def emergency_donor_pool(blood_request, limit=50):
        """Candidate donors for an emergency shortage. Inclusion is based on
        configured compatibility rules + ACTIVE donor status only; every
        candidate still requires full screening before donating."""
        from donors.models import Donor
        from donors.services import DonorEligibilityService

        item = blood_request.items.select_related("blood_type", "component").first()
        if item is None:
            return [], 0
        candidates = CompatibilityService.find_compatible_donors(item.blood_type, item.component, limit=limit)
        pool, shortage = [], item.outstanding
        for donor in candidates:
            result = DonorEligibilityService.evaluate(donor)
            if result.status in ("ELIGIBLE", "REQUIRES_STAFF_REVIEW"):
                pool.append({"donor": donor, "eligibility": result})
        return pool, max(0, shortage - len(pool))

    @staticmethod
    def trigger_emergency_notification(blood_request, *, actor=None, request=None, donor_limit=50):
        """Shortage workflow: notify eligible compatible donors."""
        from notifications.services import NotificationService

        pool, _ = BloodRequestService.emergency_donor_pool(blood_request, limit=donor_limit)
        if not pool:
            raise RequestError(
                "No eligible compatible donors found. Check compatibility rules and donor records."
            )
        sent, failed = NotificationService.emergency_donor_notification(
            blood_request, [entry["donor"] for entry in pool], actor=actor, request=request,
        )
        return sent, failed
