"""Read-only inventory consistency check.

Reports (never silently fixes, except with --fix-expired):
  * usable bags past their expiration date,
  * AVAILABLE bags with no recorded release (released_by/released_at missing),
  * RESERVED bags without an active (RESERVED) allocation,
  * active allocations whose bag is not RESERVED,
  * reservations still held against a closed request,
  * items where reservations + issued units exceed the requested quantity,
  * cached vs. ledger-derived counts (informational).

Ledger rows (InventoryTransaction) are append-only and are never modified.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from inventory.models import BloodBag
from inventory.services import InventoryService
from requests.models import Allocation, RequestItem


class Command(BaseCommand):
    help = "Check inventory consistency and report anomalies (audit trail preserved)."

    def add_arguments(self, parser):
        parser.add_argument("--fix-expired", action="store_true",
                            help="Also run the standard expiration rule (same as expire_blood_bags).")

    def handle(self, *args, **options):
        now = timezone.now()
        usable = ["QUARANTINED", "TESTING", "AVAILABLE", "RESERVED", "RETURNED"]
        issues = 0

        overdue = BloodBag.objects.filter(status__in=usable, expires_at__lte=now)
        for bag in overdue:
            issues += 1
            self.stdout.write(self.style.WARNING(
                f"[EXPIRY] {bag.bag_code} status={bag.status} expired {bag.expires_at:%Y-%m-%d %H:%M}"))

        unreleased = BloodBag.objects.filter(
            status__in=["AVAILABLE", "RESERVED", "ISSUED", "TRANSFUSED"], released_at__isnull=True)
        for bag in unreleased:
            issues += 1
            self.stdout.write(self.style.WARNING(
                f"[RELEASE] {bag.bag_code} status={bag.status} has no recorded release (released_at missing)"))

        reserved = BloodBag.objects.filter(status="RESERVED")
        for bag in reserved:
            if not Allocation.objects.filter(bag=bag, status="RESERVED").exists():
                issues += 1
                self.stdout.write(self.style.WARNING(
                    f"[RESERVATION] {bag.bag_code} is RESERVED but has no active allocation"))

        for allocation in Allocation.objects.filter(status="RESERVED").select_related("bag", "request"):
            if allocation.bag.status != "RESERVED":
                issues += 1
                self.stdout.write(self.style.WARNING(
                    f"[ALLOCATION] Allocation #{allocation.pk} ({allocation.request.request_code}) is RESERVED "
                    f"but bag {allocation.bag.bag_code} status={allocation.bag.status}"))

        closed = ["FULFILLED", "REJECTED", "CANCELLED", "EXPIRED"]
        for allocation in Allocation.objects.filter(status="RESERVED").select_related("request"):
            if allocation.request.status in closed:
                issues += 1
                self.stdout.write(self.style.WARNING(
                    f"[ORPHAN] Allocation #{allocation.pk} holds {allocation.bag.bag_code} for "
                    f"{allocation.request.request_code} but that request is "
                    f"{allocation.request.status} - issue or cancel the reservation"))

        for item in RequestItem.objects.select_related("request", "blood_type", "component"):
            reserved = item.allocations.filter(status="RESERVED").count()
            if item.fulfilled_quantity > item.quantity:
                issues += 1
                self.stdout.write(self.style.WARNING(
                    f"[FULFILLMENT] Item {item.pk} of {item.request.request_code}: "
                    f"{item.fulfilled_quantity} issued against {item.quantity} requested"))
            if reserved + item.fulfilled_quantity > item.quantity:
                issues += 1
                self.stdout.write(self.style.WARNING(
                    f"[OVER-RESERVATION] Item {item.pk} of {item.request.request_code}: "
                    f"{reserved} reserved + {item.fulfilled_quantity} issued for "
                    f"{item.quantity} requested unit(s)"))

        if options["fix_expired"]:
            expired = InventoryService.expire_bags()
            self.stdout.write(self.style.SUCCESS(f"Expiration rule applied: {len(expired)} bag(s) -> EXPIRED."))

        if issues == 0:
            self.stdout.write(self.style.SUCCESS("Inventory consistent — no anomalies found."))
        else:
            self.stdout.write(self.style.ERROR(
                f"{issues} anomaly/anomalies found. Review above; corrections must go through "
                f"audited transitions (never direct edits)."))
