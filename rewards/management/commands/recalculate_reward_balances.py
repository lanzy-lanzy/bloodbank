"""Recompute donors' cached point balances from the append-only point ledger.

The ledger (PointTransaction) is the source of truth and is never modified.
Only the cached Donor.points_balance field is corrected.
"""

from django.core.management.base import BaseCommand

from donors.models import Donor
from rewards.services import RewardService


class Command(BaseCommand):
    help = "Recalculate cached donor point balances from the ledger."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Report mismatches without saving corrections.")

    def handle(self, *args, **options):
        dry = options["dry_run"]
        checked = fixed = 0
        for donor in Donor.all_objects.all():
            ledger_balance = RewardService._ledger_balance(donor)
            checked += 1
            if donor.points_balance != ledger_balance:
                fixed += 1
                self.stdout.write(self.style.WARNING(
                    f"{donor.donor_code} ({donor.full_name}): cached={donor.points_balance} "
                    f"ledger={ledger_balance}"
                    + (" [dry-run, not saved]" if dry else " -> corrected")))
                if not dry:
                    donor.points_balance = ledger_balance
                    donor.save(update_fields=["points_balance"])
        self.stdout.write(self.style.SUCCESS(
            f"Checked {checked} donor(s); {fixed} mismatch(es)"
            + (" found (dry run)." if dry else " corrected.")))
