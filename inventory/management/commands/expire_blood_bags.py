"""Move blood bags past their expiration date to EXPIRED (audited, idempotent)."""

from django.core.management.base import BaseCommand

from inventory.services import InventoryService


class Command(BaseCommand):
    help = "Expire blood bags whose expiration date has passed."

    def handle(self, *args, **options):
        expired = InventoryService.expire_bags()
        if expired:
            for bag in expired:
                self.stdout.write(f"EXPIRED {bag.bag_code} ({bag.blood_type}, {bag.component})")
            self.stdout.write(self.style.SUCCESS(f"{len(expired)} bag(s) moved to EXPIRED."))
        else:
            self.stdout.write("No bags past expiration. Nothing to do.")
