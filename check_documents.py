"""Smoke: every printable-document endpoint against the seeded dev database."""
import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.conf import settings  # noqa: E402

settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ["testserver"]

from django.test import Client  # noqa: E402
from django.urls import reverse  # noqa: E402

from accounts.models import User  # noqa: E402
from reports.engine import REPORTS  # noqa: E402

PASSWORD = "Demo12345!"
fails = []


def probe(label, resp, want_status=200, want_pdf=False):
    ok = resp.status_code == want_status
    if ok and want_pdf:
        ok = (want_pdf in resp["Content-Type"]
              and resp.content.startswith(b"%PDF-")
              and len(resp.content) > 900)
    print(("OK   " if ok else "FAIL "), f"{label:44s}", resp.status_code,
          resp["Content-Type"], len(resp.content))
    if not ok:
        fails.append(label)


def main():
    for username in ("admin", "staff"):
        user = User.objects.filter(username=username).first()
        if not user:
            print("seed user missing; run seed_demo")
            return 2
        c = Client()
        c.login(username=username, password=PASSWORD)

        for key, report in REPORTS.items():
            if user.role not in report["roles"]:
                continue
            probe(f"{username} doc:{key}", c.get(reverse("reports:document", kwargs={"key": key})))
            probe(f"{username} pdf:{key}", c.get(reverse("reports:pdf", kwargs={"key": key})),
                  want_pdf="application/pdf")

        probe(f"{username} statement", c.get(reverse("inventory:statement")))
        probe(f"{username} statement.pdf", c.get(reverse("inventory:statement_pdf")),
              want_pdf="application/pdf")
        probe(f"{username} statement filtered",
              c.get(reverse("inventory:statement") + "?status=AVAILABLE&expiry=active"))
        probe(f"{username} statement.pdf filtered",
              c.get(reverse("inventory:statement_pdf") + "?status=AVAILABLE"),
              want_pdf="application/pdf")
        for name in ("inventory:dashboard", "inventory:bag_list", "inventory:transactions",
                     "reports:center"):
            probe(f"{username} {name}", c.get(reverse(name)))
        probe(f"{username} report run",
              c.get(reverse("reports:run", kwargs={"key": "inventory"})))

    # Negative access: the new endpoints must be exactly as locked as the pages.
    donor = User.objects.filter(role="DONOR").first()
    if donor:
        c = Client()
        c.login(username=donor.username, password=PASSWORD)
        for name in ("reports:document", "reports:pdf"):
            probe(f"donor denied {name}", c.get(reverse(name, kwargs={"key": "inventory"})),
                  want_status=403)
        for name in ("inventory:statement", "inventory:statement_pdf"):
            probe(f"donor denied {name}", c.get(reverse(name)), want_status=403)

    print()
    if fails:
        print(f"{len(fails)} FAILURES:")
        for name in fails:
            print(" -", name)
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
