"""Backfill donor records for approved DONOR registrations that have none.

Why this exists: before the registration-approval flow started creating a Donor
record (RegistrationService._link_donor_profile), an approved donor's account was
activated but left with no linked Donor, so their dashboard showed "No donor
record linked to your account". Approval is one-shot, so those existing accounts
can never be re-approved to pick up the record.

Safety (agents.md): this command is INSERT-ONLY and idempotent. It never edits,
deletes or resets anything — it skips any user that already has a donor profile
and only creates the missing record from data already captured on the approved
registration. No clinical/eligibility value is invented: `sex` defaults to the
neutral UNDISCLOSED placeholder for staff to complete later, exactly as the live
approval path does. Run with --dry-run first to preview.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import RegistrationRequest, User
from accounts.services import RegistrationService
from audit import services as audit


class Command(BaseCommand):
    help = ("Create the missing Donor record for approved DONOR registrations "
            "whose active user has none (insert-only, idempotent).")

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be created without writing anything.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        candidates = (
            RegistrationRequest.objects
            .filter(role=User.Role.DONOR,
                    status=RegistrationRequest.Status.APPROVED,
                    user__isnull=False)
            .select_related("user", "blood_type")
            .order_by("created_at")
        )

        created = 0
        skipped_existing = 0
        needs_configuration = 0
        for registration in candidates:
            user = registration.user
            if getattr(user, "donor_profile", None) is not None:
                skipped_existing += 1
                continue
            if registration.date_of_birth is None:
                needs_configuration += 1
                self.stdout.write(
                    f"SKIP (no date of birth on file, REQUIRES STAFF CONFIGURATION): "
                    f"{user.username}")
                continue
            if dry_run:
                self.stdout.write(
                    f"WOULD CREATE donor record for {user.username}")
                created += 1
                continue
            with transaction.atomic():
                donor = RegistrationService._link_donor_profile(registration, user)
                if donor is None:  # re-check inside the tx (concurrency guard)
                    skipped_existing += 1
                    continue
                audit.log(action="DONOR_PROFILE_BACKFILLED", module="accounts",
                          obj=donor,
                          description=(
                              f"Backfilled donor record {donor.donor_code} for approved "
                              f"donor {user.username} (created by management command, "
                              "insert-only repair of a pre-approval-link account)."))
                created += 1
                self.stdout.write(
                    f"CREATED {donor.donor_code} for {user.username}")

        summary = (f"{created} donor record(s) "
                   f"{'to create' if dry_run else 'created'}, "
                   f"{skipped_existing} already linked, "
                   f"{needs_configuration} requiring staff configuration.")
        if dry_run:
            self.stdout.write(self.style.WARNING(f"DRY RUN - nothing written. {summary}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Done. {summary}"))
