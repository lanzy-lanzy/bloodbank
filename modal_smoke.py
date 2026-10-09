"""Modal-path smoke test (read-mostly, rolls back any write).

The plain e2e walkers (check_e2e*.py) hit CRUD pages as full pages. This
script verifies the OTHER half of the dual-rendering contract from
core/modals.py: the same URL requested with `HX-Request` must return a modal
fragment (components/modal_shell.html), not a full page, and successful
hx-posts must answer with the `bb:modal-success` HX-Trigger.

Run:  python modal_smoke.py        (needs a seeded dev DB: manage.py seed_demo)
Exit: 0 = all OK.
"""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.conf import settings as dj_settings  # noqa: E402

from accounts.models import User  # noqa: E402
from appointments.models import Appointment  # noqa: E402
from donors.models import Donor, DonorScreening  # noqa: E402
from donations.models import Donation  # noqa: E402
from inventory.models import BloodBag  # noqa: E402
from requests.models import BloodRequest, Organization  # noqa: E402
from rewards.models import RewardRule  # noqa: E402

dj_settings.ALLOWED_HOSTS = list(dj_settings.ALLOWED_HOSTS) + ["testserver"]

PASSWORD = "Demo12345!"
FAILS = []


def check(label, ok):
    print(("ok   " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)


def fragment(client, url, label, *, expect_contains=()):
    r = client.get(url, HTTP_HX_REQUEST="true")
    body = r.content.decode("utf-8", "replace")
    ok = r.status_code == 200 and "bbModal()" in body and "<!DOCTYPE" not in body
    for needle in expect_contains:
        ok = ok and needle in body
    check(f"modal fragment {label} {url}", ok)
    return r


def fullpage(client, url, label):
    r = client.get(url)
    body = r.content.decode("utf-8", "replace")
    check(f"full page      {label} {url}", r.status_code == 200 and "<!DOCTYPE" in body)
    return r


def login(role):
    user = User.objects.filter(role=role, is_active=True, is_locked=False).order_by("id").first()
    if user is None:
        return None, None
    c = Client()
    ok = c.login(username=user.username, password=PASSWORD)
    return (c, user) if ok else (None, user)


def main():
    donor = Donor.objects.order_by("id").first()
    donation = Donation.objects.order_by("id").first()
    bag = BloodBag.objects.order_by("id").first()
    request = BloodRequest.objects.order_by("id").first()
    org = Organization.objects.order_by("id").first()
    appt = Appointment.objects.order_by("id").first()
    rule = RewardRule.objects.order_by("id").first()
    screening = DonorScreening.objects.order_by("id").first()

    with transaction.atomic():
        # --- staff/admin CRUD entry points -------------------------------------
        c, admin = login("ADMIN")
        if c is None:
            check("ADMIN login (Demo password / seed_demo run?)", False)
            raise SystemExit(1)

        fragment(c, "/donors/create/", "donors", )
        fullpage(c, "/donors/create/", "donors")
        if donor:
            fragment(c, f"/donors/{donor.pk}/", "donor detail")
            fragment(c, f"/donors/{donor.pk}/edit/", "donor edit")
        fragment(c, "/accounts/users/create/", "user create")
        if admin:
            fragment(c, f"/accounts/users/{admin.pk}/toggle-lock/", "lock confirm")
        fragment(c, "/accounts/profile/", "profile")
        fragment(c, "/accounts/password-change/", "password change")
        fragment(c, "/appointments/create/", "appointment create")
        if appt:
            from appointments.views import VALID_STATUS_MOVES
            moves = VALID_STATUS_MOVES.get(appt.status, [])
            if moves:
                fragment(c, f"/appointments/{appt.pk}/status/?to={moves[0]}", "appt status confirm")
        fragment(c, "/donations/create/", "donation create")
        if donation:
            fragment(c, f"/donations/{donation.pk}/", "donation detail")
        if bag:
            # The bag record is deliberately NOT a modal: it carries the
            # record-test-result form plus the guarded release/transition
            # actions, so it is a working surface and stays a full page. Assert
            # that here too, because this script is where the modal contract is
            # policed — an HX-Request must NOT be able to turn it into a
            # fragment (that would bury the forms in a nested scroll box).
            r = c.get(f"/inventory/bags/{bag.pk}/", HTTP_HX_REQUEST="true")
            body = r.content.decode("utf-8", "replace")
            check(f"bag detail stays a full page even for HX-Request "
                  f"/inventory/bags/{bag.pk}/",
                  r.status_code == 200 and "<!DOCTYPE" in body
                  and "bbModal()" not in body)
            fullpage(c, f"/inventory/bags/{bag.pk}/", "bag detail")
        fragment(c, "/inventory/bags/register/", "bag register")
        fragment(c, "/requests/create/", "request create")
        if request:
            fragment(c, f"/requests/{request.pk}/", "request detail")
        if org:
            fragment(c, f"/requests/organizations/{org.pk}/edit/", "org edit")
        fragment(c, "/rewards/admin/rules/create/", "rule create")
        if rule:
            fragment(c, f"/rewards/admin/rules/{rule.pk}/edit/", "rule edit")
        fragment(c, "/notifications/templates/create/", "notif template create")

        # --- the negative half: plain GET still renders the full page ---------
        fullpage(c, "/donors/create/", "donors")

        # --- guarded hx-post: empty body + bb:modal-success trigger -----------
        r = c.post("/accounts/profile/", {"first_name": "Modal", "last_name": admin.last_name or "Smoke",
                                          "email": admin.email or "smoke@example.com", "phone": admin.phone or ""},
                   HTTP_HX_REQUEST="true")
        ok = (r.status_code == 200 and r.content == b""
              and "bb:modal-success" in r.headers.get("HX-Trigger", ""))
        check("hx-post success -> empty body + bb:modal-success", ok)

        # invalid hx-post re-renders the modal with errors (still a fragment)
        bad = c.get("/donors/create/", HTTP_HX_REQUEST="true")
        r = c.post("/donors/create/", {}, HTTP_HX_REQUEST="true")
        frag_ok = r.status_code == 200 and "bbModal()" in r.content.decode("utf-8", "replace")
        check("invalid hx-post re-renders modal fragment (was "
              f"{bad.status_code} GET)", frag_ok)

        # plain POST fallback (no HX-Request) must still redirect
        r = c.post("/accounts/profile/", {"first_name": "Plain", "last_name": admin.last_name or "Smoke",
                                          "email": admin.email or "smoke@example.com", "phone": admin.phone or ""})
        check("plain POST still redirects", r.status_code in (302, 303))

        # --- requester role sees only own-request modals ----------------------
        c2, _ = login("REQUESTER")
        if c2 and request:
            mine = BloodRequest.objects.filter(organization=request.organization).first()
            if mine:
                fragment(c2, f"/requests/{mine.pk}/", "own request")
                other = BloodRequest.objects.exclude(organization=request.organization).first()
                if other:
                    r = c2.get(f"/requests/{other.pk}/", HTTP_HX_REQUEST="true")
                    check("requester cannot open foreign request modal", r.status_code in (403, 404))
        transaction.set_rollback(True)

    print("-" * 70)
    if FAILS:
        print(f"FAILURES: {len(FAILS)}")
        for f in FAILS:
            print("  -", f)
        raise SystemExit(1)
    print("ALL OK")


if __name__ == "__main__":
    main()
