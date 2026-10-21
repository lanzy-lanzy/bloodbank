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

from accounts.models import RegistrationRequest, User  # noqa: E402
from donors.models import DonorInterviewSheet  # noqa: E402
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
    # The interview sheet a pending application will create: previewed by staff
    # before approval, so it must be printable exactly like the real sheet.
    pending = (RegistrationRequest.objects
               .filter(role="DONOR", status="PENDING", interview__isnull=False)
               .order_by("pk").first())

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

        # The donor interview sheet is a printable document too: preview and PDF
        # must both render the same spec (seed_demo generates one per donor).
        sheet = DonorInterviewSheet.objects.first()
        if sheet:
            probe(f"{username} interview sheet",
                  c.get(reverse("donors:interview_sheet_document",
                                kwargs={"pk": sheet.donor_id})))
            probe(f"{username} interview sheet.pdf",
                  c.get(reverse("donors:interview_sheet_pdf",
                                kwargs={"pk": sheet.donor_id})),
                  want_pdf="application/pdf")
        else:
            print("SKIP interview sheet: no sheet on file; run seed_demo")
            fails.append("interview sheet missing from seeded DB")

        if pending:
            probe(f"{username} pending sheet preview",
                  c.get(reverse("accounts:registration_sheet_preview",
                                kwargs={"pk": pending.pk})))
            probe(f"{username} pending sheet preview.pdf",
                  c.get(reverse("accounts:registration_sheet_pdf",
                                kwargs={"pk": pending.pk})),
                  want_pdf="application/pdf")
        else:
            # The review queue may legitimately be empty (every demo application
            # has been worked through). check_e2e_post.py always covers the
            # preview with its own rolled-back registration, so a missing row is
            # a skip here, not a defect.
            print("SKIP pending sheet preview: no pending donor registration "
                  "(submit one from /register/ or re-run seed_demo)")

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
        if pending:
            # A donor never reads another applicant's paperwork - not even the
            # preview of a sheet that does not exist yet.
            for name in ("accounts:registration_sheet_preview",
                         "accounts:registration_sheet_pdf"):
                probe(f"donor denied {name}",
                      c.get(reverse(name, kwargs={"pk": pending.pk})), want_status=403)

        # Staff-side sheets stay staff-side: a donor may only print their own.
        sheet = DonorInterviewSheet.objects.first()
        if sheet:
            pk = sheet.donor_id
            probe("donor denied interview sheet",
                  c.get(reverse("donors:interview_sheet", kwargs={"pk": pk})),
                  want_status=403)
            probe("donor denied interview sheet preview",
                  c.get(reverse("donors:interview_sheet_document", kwargs={"pk": pk})),
                  want_status=403)
            probe("donor denied interview sheet.pdf",
                  c.get(reverse("donors:interview_sheet_pdf", kwargs={"pk": pk})),
                  want_status=403)
            own = getattr(donor, "donor_profile", None)
            if own and getattr(own, "interview_sheet", None):
                probe("donor own interview sheet",
                      c.get(reverse("donors:my_interview_sheet")))
                probe("donor own interview sheet.pdf",
                      c.get(reverse("donors:my_interview_sheet_pdf")),
                      want_pdf="application/pdf")

    requester = User.objects.filter(role="REQUESTER").first()
    sheet = DonorInterviewSheet.objects.first()
    if requester and sheet:
        c = Client()
        c.login(username=requester.username, password=PASSWORD)
        for name in ("donors:interview_sheet_document", "donors:interview_sheet_pdf"):
            probe(f"requester denied {name}",
                  c.get(reverse(name, kwargs={"pk": sheet.donor_id})), want_status=403)
    if requester and pending:
        c = Client()
        c.login(username=requester.username, password=PASSWORD)
        for name in ("accounts:registration_sheet_preview",
                     "accounts:registration_sheet_pdf"):
            probe(f"requester denied {name}",
                  c.get(reverse(name, kwargs={"pk": pending.pk})), want_status=403)

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
