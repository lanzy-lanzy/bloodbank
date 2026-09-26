"""Delete expired session rows (sessions past their expire_date).

Only removes sessions Django has already marked as expired; active sessions
are never touched.
"""

from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Remove expired sessions from the session store."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Only report how many sessions would be removed.")

    def handle(self, *args, **options):
        qs = Session.objects.filter(expire_date__lt=timezone.now())
        count = qs.count()
        if options["dry_run"]:
            self.stdout.write(f"{count} expired session(s) would be removed (dry run).")
            return
        qs.delete()
        self.stdout.write(self.style.SUCCESS(f"Removed {count} expired session(s)."))
