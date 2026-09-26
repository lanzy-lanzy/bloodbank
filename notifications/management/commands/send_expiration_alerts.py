"""Alert staff/admin users about blood bags expiring soon (in-app notifications).

Uses the 'expiration_alert' notification template. Deduplicates: will not send
another alert for the same bag within the alert window (default 24 h).
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User
from inventory.services import InventoryService
from notifications.models import Notification, NotificationTemplate
from notifications.services import NotificationService


class Command(BaseCommand):
    help = "Notify staff about blood bags expiring soon."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None,
                            help="Override the configured expiring_soon_days window.")

    def handle(self, *args, **options):
        bags = list(InventoryService.expiring_soon(days=options["days"]))
        if not bags:
            self.stdout.write("No bags expiring soon. Nothing to send.")
            return

        template = NotificationTemplate.objects.filter(code="expiration_alert", is_active=True).first()
        if template is None:
            self.stdout.write(self.style.ERROR(
                "No active 'expiration_alert' notification template. "
                "Create one under Notifications -> Templates (or run seed_demo)."))
            return

        cutoff = timezone.now() - timedelta(hours=24)

        staff = User.objects.filter(role__in=["ADMIN", "STAFF"], is_active=True)
        sent = 0
        for bag in bags:
            marker = f"expiration_alert:{bag.bag_code}"
            if Notification.objects.filter(template=template, body__contains=marker,
                                           sent_at__gte=cutoff).exists():
                continue
            for user in staff:
                NotificationService.send_to_user(
                    user, "expiration_alert",
                    {
                        "quantity": 1,
                        "bag_code": f"{bag.bag_code} ({bag.blood_type} {bag.component}, "
                                    f"expires {timezone.localtime(bag.expires_at):%Y-%m-%d %H:%M}) "
                                    f"[{marker}]",
                    },
                    channels=["in_app"],
                )
                sent += 1
        self.stdout.write(self.style.SUCCESS(
            f"Expiration alerts: {len(bags)} bag(s) expiring soon, {sent} notification(s) sent."))
