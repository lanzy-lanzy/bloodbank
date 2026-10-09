"""Role-by-role page walkthrough against the DEV database (smoke / E2E helper).

GET-walks every major page as admin / staff / donor / requester using the
Django test client, plus negative access checks (data scoping rules).
Read-only: no POSTs, no data mutation. Usage:
    ./.venv/Scripts/python.exe check_e2e.py
"""
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ["testserver"]

from django.test import Client  # noqa: E402
from django.urls import reverse  # noqa: E402

from appointments.models import Appointment  # noqa: E402
from donors.models import Donor  # noqa: E402
from donations.models import Donation  # noqa: E402
from inventory.models import BloodBag  # noqa: E402
from notifications.models import Notification, NotificationTemplate  # noqa: E402
from reports.engine import REPORTS  # noqa: E402
from requests.models import Allocation, BloodRequest, Organization  # noqa: E402
from rewards.models import Reward  # noqa: E402
from accounts.models import RegistrationRequest, User  # noqa: E402

PASSWORD = "Demo12345!"

failures = []
checks = 0


def check(user, name, url, *expected):
    """GET url as user; status must be in expected set."""
    global checks
    if len(expected) == 1 and isinstance(expected[0], (set, frozenset)):
        expected = expected[0]
    else:
        expected = set(expected)
    checks += 1
    c = Client()
    ok = c.login(username=user.username, password=PASSWORD)
    if not ok:
        failures.append(f"{name}: login failed for {user.username}")
        return None
    try:
        resp = c.get(url, follow=False)
        code = resp.status_code
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{name}: EXCEPTION as {user.username} {url} -> {type(exc).__name__}: {exc}")
        return None
    if code not in expected:
        failures.append(f"{name}: {code} (expected {expected}) as {user.username} {url}")
    return resp


def main():
    global checks
    try:
        admin = User.objects.get(username="admin", role="ADMIN")
        staff = User.objects.get(username="staff", role="STAFF")
        donor_user = User.objects.get(username="donor", role="DONOR")
        requester = User.objects.get(username="requester", role="REQUESTER")
    except User.DoesNotExist as exc:
        print(f"Seed users missing ({exc}). Run seed_demo first.")
        return 2

    donor = getattr(donor_user, "donor_profile", None)
    my_profile = getattr(requester, "requester_profile", None)

    sample_donor = Donor.objects.first()
    sample_donation = Donation.objects.exclude(status="CANCELLED").first()
    sample_bag = BloodBag.objects.first()
    sample_request = BloodRequest.objects.first()
    sample_org = Organization.objects.first()
    sample_appt = Appointment.objects.first()
    sample_template = NotificationTemplate.objects.first()
    sample_notif = Notification.objects.first()
    sample_notif_pk = sample_notif.pk if sample_notif else None
    sample_alloc = Allocation.objects.first()
    sample_reward = Reward.objects.first()

    # ---------- ADMIN: every major page ----------
    admin_pages = [
        ("dashboard", reverse("core:dashboard"), {200}),
        ("donors list", reverse("donors:list"), {200}),
        ("donor detail", reverse("donors:detail", kwargs={"pk": sample_donor.pk}), {200}),
        ("donor create", reverse("donors:create"), {200}),
        ("donor edit", reverse("donors:edit", kwargs={"pk": sample_donor.pk}), {200}),
        ("donor my profile", reverse("donors:my_profile"), {200, 403}),
        ("my profile (no donor link = 403 ok)", reverse("donors:my_profile"), {200, 403}),
        ("donor screening create", reverse("donors:screening_create", kwargs={"pk": sample_donor.pk}), {200}),
        ("appointments list", reverse("appointments:list"), {200}),
        ("appointments create", reverse("appointments:create"), {200}),
        ("appointments edit", reverse("appointments:edit", kwargs={"pk": sample_appt.pk}), {200}),
        ("appointments my", reverse("appointments:my_appointments"), {200, 403}),
        ("donations list", reverse("donations:list"), {200}),
        ("donation detail", reverse("donations:detail", kwargs={"pk": sample_donation.pk}), {200}),
        ("inventory dashboard", reverse("inventory:dashboard"), {200}),
        ("bag list", reverse("inventory:bag_list"), {200}),
        ("bag detail", reverse("inventory:bag_detail", kwargs={"pk": sample_bag.pk}), {200}),
        ("bag register", reverse("inventory:bag_register"), {200}),
        ("transactions", reverse("inventory:transactions"), {200}),
        ("inventory statement preview", reverse("inventory:statement"), {200}),
        ("inventory statement pdf", reverse("inventory:statement_pdf"), {200}),
        ("compat check", reverse("inventory:compat_check"), {200}),
        ("requests list", reverse("requests:list"), {200}),
        ("request detail", reverse("requests:detail", kwargs={"pk": sample_request.pk}), {200}),
        ("request create", reverse("requests:create"), {200}),
        ("walk-in desk", reverse("requests:walk_in_desk"), {200}),
        ("walk-in desk closed scope", reverse("requests:walk_in_desk") + "?scope=closed", {200}),
        ("walk-in sidebar badge", reverse("requests:walk_in_badge"), {200}),
        ("organizations", reverse("requests:organization_list"), {200}),
        ("organization edit", reverse("requests:organization_edit", kwargs={"pk": sample_org.pk}), {200}),
        ("notifications inbox", reverse("notifications:inbox"), {200}),
        ("delivery list", reverse("notifications:delivery_list"), {200}),
        ("template list", reverse("notifications:template_list"), {200}),
        ("template create", reverse("notifications:template_create"), {200}),
        ("template edit", reverse("notifications:template_edit", kwargs={"pk": sample_template.pk}), {200}),
        ("respond pk", reverse("notifications:respond", kwargs={"pk": sample_notif_pk}), {200}),
        ("rewards admin", reverse("rewards:admin_overview"), {200}),
        ("rewards my", reverse("rewards:my_rewards"), {200, 403}),
        ("reports center", reverse("reports:center"), {200}),
        ("settings index", reverse("settings_app:index"), {200}),
        ("settings blood bank", reverse("settings_app:blood_bank"), {200}),
        ("audit list", reverse("audit:list"), {200}),
        ("registrations list", reverse("accounts:registration_list"), {200}),
        ("registrations list filtered", reverse("accounts:registration_list") + "?status=PENDING", {200}),
        ("requests sidebar badge", reverse("requests:badge"), {200}),
        ("inventory sidebar badge", reverse("inventory:badge"), {200}),
        ("registrations sidebar badge", reverse("accounts:registration_badge"), {200}),
    ]
    sample_registration = RegistrationRequest.objects.first()
    if sample_registration:
        admin_pages.append(("registration review",
                            reverse("accounts:registration_review",
                                    kwargs={"pk": sample_registration.pk}), {200}))
    for key in REPORTS:
        admin_pages.append((f"report {key}", reverse("reports:run", kwargs={"key": key}), {200}))
        admin_pages.append((f"export {key}", reverse("reports:export", kwargs={"key": key}), {200}))
        # Printable outputs of every report: the print preview and the PDF must
        # be reachable exactly when the report itself is.
        admin_pages.append((f"document {key}", reverse("reports:document", kwargs={"key": key}), {200}))
        admin_pages.append((f"pdf {key}", reverse("reports:pdf", kwargs={"key": key}), {200}))
    for name, url, expected in admin_pages:
        check(admin, name, url, *expected)

    # ---------- STAFF: workflow pages OK, config pages forbidden ----------
    staff_pages = [
        ("dashboard", reverse("core:dashboard"), {200}),
        ("donors list", reverse("donors:list"), {200}),
        ("inventory dashboard", reverse("inventory:dashboard"), {200}),
        ("bag detail", reverse("inventory:bag_detail", kwargs={"pk": sample_bag.pk}), {200}),
        ("request detail", reverse("requests:detail", kwargs={"pk": sample_request.pk}), {200}),
        ("walk-in desk", reverse("requests:walk_in_desk"), {200}),
        ("walk-in sidebar badge", reverse("requests:walk_in_badge"), {200}),
        ("appointments list", reverse("appointments:list"), {200}),
        ("delivery list", reverse("notifications:delivery_list"), {200}),
        ("reports center", reverse("reports:center"), {200}),
        ("audit list", reverse("audit:list"), {200, 403}),
        ("requests sidebar badge", reverse("requests:badge"), {200}),
        ("inventory sidebar badge", reverse("inventory:badge"), {200}),
        # staff must NOT manage critical configuration
        ("settings index (deny)", reverse("settings_app:index"), {302, 403}),
        ("blood bank config (deny)", reverse("settings_app:blood_bank"), {302, 403}),
        ("template list (deny)", reverse("notifications:template_list"), {302, 403}),
        ("registrations list (deny)", reverse("accounts:registration_list"), {302, 403}),
        ("registrations sidebar badge (deny)", reverse("accounts:registration_badge"), {302, 403}),
        ("organization edit (deny)", reverse("requests:organization_edit", kwargs={"pk": sample_org.pk}), {200, 302, 403}),
    ]
    for key, report in REPORTS.items():
        if "ADMIN" not in report["roles"]:  # audit-only report is admin, others staff too
            staff_pages.append((f"report {key}", reverse("reports:run", kwargs={"key": key}), {200}))
            staff_pages.append((f"document {key}", reverse("reports:document", kwargs={"key": key}), {200}))
    # The inventory statement is a staff document, so staff must get it too.
    staff_pages.append(("inventory statement", reverse("inventory:statement"), {200}))
    staff_pages.append(("inventory statement pdf", reverse("inventory:statement_pdf"), {200}))
    # ... but the admin-only audit report stays un-printable for staff.
    staff_pages.append(("audit report document (deny)",
                        reverse("reports:document", kwargs={"key": "audit"}), {403}))
    staff_pages.append(("audit report pdf (deny)",
                        reverse("reports:pdf", kwargs={"key": "audit"}), {403}))
    for name, url, expected in staff_pages:
        check(staff, name, url, *expected)

    # ---------- DONOR: own data only ----------
    if donor:
        check(donor_user, "donor dashboard", reverse("core:dashboard"), {200})
        check(donor_user, "donor my profile", reverse("donors:my_profile"), {200})
        check(donor_user, "donor profile edit", reverse("donors:my_profile_edit"), {200})
        check(donor_user, "donor appointments", reverse("appointments:my_appointments"), {200})
        check(donor_user, "donor history", reverse("donations:my_history"), {200})
        check(donor_user, "donor rewards", reverse("rewards:my_rewards"), {200})
        check(donor_user, "donor inbox", reverse("notifications:inbox"), {200})
        # negative: no access to other donors / staff areas
        check(donor_user, "donors list (deny)", reverse("donors:list"), {302, 403})
        other = Donor.objects.exclude(pk=donor.pk).first()
        if other:
            check(donor_user, "other donor detail (deny)", reverse("donors:detail", kwargs={"pk": other.pk}), {403})
        check(donor_user, "inventory (deny)", reverse("inventory:dashboard"), {302, 403})
        check(donor_user, "inventory sidebar badge (deny)", reverse("inventory:badge"), {302, 403})
        check(donor_user, "requests (deny)", reverse("requests:list"), {403})
        check(donor_user, "requests sidebar badge (deny)", reverse("requests:badge"), {403})
        check(donor_user, "walk-in desk (deny)", reverse("requests:walk_in_desk"), {302, 403})
        check(donor_user, "walk-in badge (deny)", reverse("requests:walk_in_badge"), {302, 403})
        check(donor_user, "registrations (deny)", reverse("accounts:registration_list"), {302, 403})
        check(donor_user, "registrations sidebar badge (deny)", reverse("accounts:registration_badge"), {302, 403})
        check(donor_user, "bag detail (deny)", reverse("inventory:bag_detail", kwargs={"pk": sample_bag.pk}), {302, 403})
        # token respond must work even for donor (their own) — use any donor notification
        dn = Notification.objects.filter(donor=donor).first()
        if dn:
            check(donor_user, "respond own", reverse("notifications:respond", kwargs={"pk": dn.pk}), {200})
            resp = check(donor_user, "respond token (own)", reverse("notifications:respond_token",
                          kwargs={"token": str(dn.response_token)}), {200})
            if resp is not None and resp.status_code == 200:
                body = resp.content.decode()
                if "availability only" not in body:
                    failures.append("respond token page missing availability disclaimer")
            # another donor's token link while logged in as this donor: staff-only path -> 403
            other_notif = Notification.objects.filter(donor__isnull=False).exclude(donor=donor).first()
            if other_notif:
                # anonymous session instead
                c = Client()
                resp = c.get(reverse("notifications:respond_token", kwargs={"token": str(other_notif.response_token)}))
                # token is a capability: page should render (anyone with the link)
                checks += 1
                if resp.status_code != 200:
                    failures.append(f"token capability link: {resp.status_code} for anon")
    else:
        failures.append("donor user has no linked Donor record")

    # ---------- REQUESTER: own organization only ----------
    check(requester, "requester dashboard", reverse("core:dashboard"), {200})
    check(requester, "requests list", reverse("requests:list"), {200})
    if my_profile and my_profile.organization:
        own = (BloodRequest.objects.filter(organization=my_profile.organization)
               .exclude(channel="WALK_IN").first())
        if own:
            check(requester, "own request detail", reverse("requests:detail", kwargs={"pk": own.pk}), {200})
        other = BloodRequest.objects.exclude(organization=my_profile.organization).first()
        if other:
            check(requester, "other org request (deny)", reverse("requests:detail", kwargs={"pk": other.pk}), {403})
    check(requester, "request create", reverse("requests:create"), {200})
    check(requester, "requests sidebar badge", reverse("requests:badge"), {200})
    # The counter queue is staff-side: a requester must not even see the page.
    check(requester, "walk-in desk (deny)", reverse("requests:walk_in_desk"), {302, 403})
    check(requester, "walk-in badge (deny)", reverse("requests:walk_in_badge"), {302, 403})
    # Walk-in records belong to the blood bank counter: no requester sees them,
    # and the channel filter must not leak them to a requester.
    walkin = BloodRequest.objects.filter(channel="WALK_IN").first()
    if walkin:
        check(requester, "walk-in request (deny)",
              reverse("requests:detail", kwargs={"pk": walkin.pk}), {403})
        req_filter_resp = check(requester, "walk-in channel filter (no leak)",
                                reverse("requests:list") + "?channel=WALK_IN", {200})
        if req_filter_resp and walkin.request_code in req_filter_resp.content.decode():
            failures.append(f"walk-in leak: {requester.username} saw {walkin.request_code} in the list")
        check(staff, "walk-in request detail",
              reverse("requests:detail", kwargs={"pk": walkin.pk}), {200})
        check(staff, "walk-in channel filter",
              reverse("requests:list") + "?channel=WALK_IN", {200})
        check(staff, "walk-in create form",
              reverse("requests:create") + "?channel=WALK_IN", {200})
    check(requester, "inventory (deny)", reverse("inventory:dashboard"), {302, 403})
    check(requester, "inventory sidebar badge (deny)", reverse("inventory:badge"), {302, 403})
    # The printable statement and the report documents must be exactly as locked
    # as the pages they are opened from - an export path is not a way around a
    # role gate.
    check(requester, "inventory statement (deny)", reverse("inventory:statement"), {302, 403})
    check(requester, "inventory statement pdf (deny)", reverse("inventory:statement_pdf"), {302, 403})
    check(requester, "report document (deny)", reverse("reports:document", kwargs={"key": "inventory"}), {302, 403})
    check(requester, "report pdf (deny)", reverse("reports:pdf", kwargs={"key": "inventory"}), {302, 403})
    check(requester, "donors (deny)", reverse("donors:list"), {403})
    check(requester, "reports (deny)", reverse("reports:center"), {302, 403})
    check(requester, "registrations (deny)", reverse("accounts:registration_list"), {302, 403})

    # ---------- ANONYMOUS: public pages open, private pages bounce to login ----------
    anon = Client()
    for name, url, expected in [
        ("public landing", reverse("core:home"), {200}),
        ("public login", reverse("accounts:login"), {200}),
        ("public register", reverse("public_registration:register"), {200}),
        ("register done", reverse("public_registration:register_done"), {200}),
        ("dashboard (redirect)", reverse("core:dashboard"), {302}),
        ("registrations (redirect)", reverse("accounts:registration_list"), {302}),
    ]:
        checks += 1
        try:
            code = anon.get(url).status_code
        except Exception as exc:  # noqa: BLE001
            failures.append(f"anon {name}: EXCEPTION {type(exc).__name__}: {exc}")
            continue
        if code not in expected:
            failures.append(f"anon {name}: {code} (expected {expected}) {url}")

    print(f"walked {checks} pages across 4 roles")
    if failures:
        print(f"\n{len(failures)} FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
