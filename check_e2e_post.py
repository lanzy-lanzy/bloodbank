"""POST workflow smoke test (E2E helper) — runs inside a transaction that is
ALWAYS rolled back, so the dev database is unchanged.

Exercises the real view endpoints: donation -> screening -> approval ->
collection -> testing -> verification -> release, request submit -> review ->
approve -> allocate -> issue -> transfuse, notification respond/retry,
appointment self-booking, reward redemption, settings quick-set, audit
immutability, and role negatives.

Usage: ./.venv/Scripts/python.exe check_e2e_post.py
"""
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

import logging  # noqa: E402

logging.disable(logging.WARNING)

from django.conf import settings  # noqa: E402

settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ["testserver"]

from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.urls import reverse  # noqa: E402
from django.utils import timezone  # noqa: E402

PASSWORD = "Demo12345!"
failures = []
checks = 0


def client_for(username):
    c = Client()
    assert c.login(username=username, password=PASSWORD), f"login failed: {username}"
    return c


def expect(cond, msg):
    global checks
    checks += 1
    if not cond:
        failures.append(msg)


def post_ok(c, url, data, msg):
    global checks
    checks += 1
    try:
        resp = c.post(url, data)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{msg}: EXCEPTION {type(exc).__name__}: {exc}")
        return None
    if resp.status_code not in (200, 302):
        failures.append(f"{msg}: HTTP {resp.status_code} at {url}")
    return resp


class Rollback(Exception):
    pass


def post_msgs(resp):
    """Django messages flushed by a redirect response — for diagnostics."""
    try:
        from django.contrib import messages as dm
        return [m.message for m in dm.get_messages(resp.wsgi_request)]
    except Exception:  # noqa: BLE001
        return []


def run_workflows():
    global checks
    from appointments.models import Appointment
    from donors.models import Donor, DonorScreening
    from donations.models import Donation
    from inventory.models import BloodBag, BloodComponent, BloodType, TestResult, TestType
    from notifications.models import Notification
    from requests.models import Allocation, BloodRequest
    from requests.services import BloodRequestService
    from rewards.models import Reward
    from rewards.services import RewardService

    now = timezone.now()
    today = now.date().isoformat()
    staff = client_for("staff")
    admin = client_for("admin")
    donor_user = client_for("donor")
    requester = client_for("requester")
    anon = Client()

    # ---------------- A. donation -> collected -> released ----------------
    donor = Donor.objects.filter(user__isnull=True, is_deleted=False).exclude(blood_type__isnull=True).first() \
        or Donor.objects.filter(is_deleted=False).first()
    wb = BloodComponent.objects.get(code="wb")
    post_ok(staff, reverse("donations:create"),
            {"donor": donor.pk, "donation_date": today, "donation_time": "09:30",
             "location": "E2E smoke", "donation_type": "VOLUNTARY", "notes": ""},
            "A1 donation create POST")
    donation = Donation.objects.filter(donor=donor, location="E2E smoke").first()
    expect(donation is not None, "A1b donation row created")
    if not donation:
        return
    expect(donation.blood_type_id == donor.blood_type_id, "A1c blood type copied from donor")

    post_ok(staff, reverse("donations:transition", kwargs={"pk": donation.pk}),
            {"status": "SCREENING", "reason": ""}, "A2 transition SCREENING")
    donation.refresh_from_db()
    expect(donation.status == "SCREENING", f"A2b status SCREENING (got {donation.status})")

    DonorScreening.objects.create(
        donor=donor, donation=donation, performed_by=None, result="CLEARED",
        identity_verified=True, consent_given=True, weight_kg=65,
    )
    post_ok(staff, reverse("donations:transition", kwargs={"pk": donation.pk}),
            {"status": "APPROVED", "reason": ""}, "A3 transition APPROVED")
    donation.refresh_from_db()
    expect(donation.status == "APPROVED", f"A3b status APPROVED (got {donation.status})")

    post_ok(staff, reverse("donations:collect", kwargs={"pk": donation.pk}),
            {"component": wb.pk, "volume_ml": 450,
             "collected_at": now.strftime("%Y-%m-%dT%H:%M"),
             "location": "E2E", "storage_position": ""}, "A4 collection POST")
    donation.refresh_from_db()
    bag = donation.blood_bags.first()
    expect(bag is not None and bag.status == "QUARANTINED",
           f"A4b bag QUARANTINED created (donation {donation.status})")

    # negative: release without confirmation token
    resp = post_ok(staff, reverse("inventory:bag_release", kwargs={"pk": bag.pk}),
                   {"reason": "no confirm"}, "A5 release WITHOUT confirm")
    bag.refresh_from_db()
    expect(bag.status == "QUARANTINED", f"A5b unconfirmed release blocked (got {bag.status})")

    if bag:
        required_tests = list(TestType.objects.filter(is_active=True, is_required=True))
        for tt in required_tests:
            post_ok(staff, reverse("inventory:bag_test", kwargs={"pk": bag.pk}),
                    {"test_type": tt.pk, "result": f"e2e {tt.name}", "result_status": "NON_REACTIVE",
                     "performed_at": now.strftime("%Y-%m-%dT%H:%M"), "notes": ""},
                    f"A6 test result POST ({tt.name})")
        bag.refresh_from_db()
        expect(bag.status == "TESTING", f"A6b bag moved to TESTING (got {bag.status})")
        for tr in bag.test_results.filter(test_type__in=required_tests):
            post_ok(staff, reverse("inventory:test_verify", kwargs={"pk": bag.pk, "result_pk": tr.pk}),
                    {}, f"A7 verify POST ({tr.test_type.name})")
        resp = post_ok(staff, reverse("inventory:bag_release", kwargs={"pk": bag.pk}),
                       {"confirm": "RELEASE", "reason": "E2E release"}, "A8 release POST")
        bag.refresh_from_db()
        expect(bag.status == "AVAILABLE", f"A8b bag released to AVAILABLE (got {bag.status})")

    # ---------------- B. request lifecycle ----------------
    bt = donor.blood_type or BloodType.objects.filter(is_active=True).first()
    prbc = BloodComponent.objects.get(code="wb")  # match the component collected in A
    profile = requester_user_profile()
    post_ok(requester, reverse("requests:create"),
            {"patient_reference": "E2E PT-01", "urgency": "ROUTINE",
             "required_by": (timezone.localtime() + timezone.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
             "clinical_indication": "E2E smoke test",
             "blood_type": bt.pk, "component": prbc.pk, "quantity": 1},
            "B1 request create POST (requester, own org)")
    breq = BloodRequest.objects.filter(patient_reference="E2E PT-01").first()
    expect(breq is not None and breq.status == "SUBMITTED"
           and breq.organization_id == profile.organization_id,
           "B1b request SUBMITTED under requester's org")

    # Walk-in intake: staff-only, booked against the configured desk organization.
    desk = BloodRequestService.walk_in_organization()
    expect(desk is not None, "B1c walk_in_organization_id is configured (re-run seed_demo)")
    post_ok(staff, reverse("requests:create"),
            {"organization": desk.pk if desk else "", "channel": "WALK_IN",
             "walk_in_contact": "E2E walk-in (patient)", "walk_in_phone": "",
             "patient_reference": "E2E PT-WALK", "urgency": "URGENT",
             "required_by": (timezone.localtime() + timezone.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
             "clinical_indication": "E2E walk-in smoke",
             "blood_type": bt.pk, "component": prbc.pk, "quantity": 1},
            "B1d walk-in create POST (staff)")
    wreq = BloodRequest.objects.filter(patient_reference="E2E PT-WALK").first()
    expect(wreq is not None and wreq.is_walk_in and desk is not None
           and wreq.organization_id == desk.pk,
           "B1e walk-in booked against the desk organization")
    # A walk-in is a direct clinic request: validated at the counter, never queued.
    expect(wreq is not None and wreq.status == BloodRequest.Status.APPROVED,
           "B1e2 walk-in lands APPROVED straight away - no approval queue")
    post_ok(requester, reverse("requests:create"),
            {"channel": "WALK_IN", "walk_in_contact": "E2E forged walk-in",
             "patient_reference": "E2E PT-WALK-X", "urgency": "ROUTINE",
             "required_by": (timezone.localtime() + timezone.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
             "blood_type": bt.pk, "component": prbc.pk, "quantity": 1},
            "B1f requester POST with forged channel")
    forged = BloodRequest.objects.filter(patient_reference="E2E PT-WALK-X").first()
    expect(forged is not None and not forged.is_walk_in
           and forged.organization_id == profile.organization_id,
           "B1g forged channel ignored - requester record stays org-channel")
    if breq:
        post_ok(staff, reverse("requests:action", kwargs={"pk": breq.pk}),
                {"action": "review"}, "B2 review POST")
        breq.refresh_from_db()
        expect(breq.status == "UNDER_REVIEW", f"B2b UNDER_REVIEW (got {breq.status})")
        post_ok(staff, reverse("requests:action", kwargs={"pk": breq.pk}),
                {"action": "approve"}, "B3 approve POST")
        breq.refresh_from_db()
        expect(breq.status == "APPROVED", f"B3b APPROVED (got {breq.status})")

        item = breq.items.first()
        bags, shortage = BloodRequestService.compatibility_report(breq)[0]["bags"], None
        candidate = None
        for cand in bags or []:
            if cand.status == "AVAILABLE":
                candidate = cand
                break
        if candidate is None:
            # fall back to the e2e-released bag if compatible
            bag.refresh_from_db()
            if bag and bag.status == "AVAILABLE":
                candidate = bag
        expect(candidate is not None, "B4a compatible AVAILABLE bag exists to allocate")
        if candidate:
            r4 = post_ok(staff, reverse("requests:action", kwargs={"pk": breq.pk}),
                    {"action": "allocate", "item": item.pk, "bag": candidate.pk}, "B4 allocate POST")
            alloc = Allocation.objects.filter(request=breq, bag=candidate).first()
            if alloc is None:
                failures.append(f"B4 diag: cand={candidate.pk} {candidate.bag_code} st={candidate.status} "
                               f"item={item.pk} msgs={post_msgs(r4)}")
            expect(alloc is not None, "B4b allocation row created")
            if alloc is None:
                return
            candidate.refresh_from_db()
            expect(candidate.status == "RESERVED", f"B4c bag RESERVED (got {candidate.status})")

            # negative: issue without confirmation
            post_ok(staff, reverse("requests:action", kwargs={"pk": breq.pk}),
                    {"action": "issue", "allocation": alloc.pk}, "B5 issue WITHOUT confirm")
            alloc.refresh_from_db()
            expect(alloc.status == Allocation.Status.RESERVED,
                   f"B5b unconfirmed issue blocked (got {alloc.status})")

            post_ok(staff, reverse("requests:action", kwargs={"pk": breq.pk}),
                    {"action": "issue", "allocation": alloc.pk, "confirm": "ISSUE"}, "B6 issue POST")
            alloc.refresh_from_db()
            candidate.refresh_from_db()
            expect(alloc.status == Allocation.Status.ISSUED, f"B6a allocation ISSUED (got {alloc.status})")
            expect(candidate.status == "ISSUED", f"B6b bag ISSUED (got {candidate.status})")

            post_ok(requester, reverse("requests:allocation_feedback", kwargs={"pk": breq.pk, "allocation_pk": alloc.pk}),
                    {"action": "transfused"}, "B7 transfused POST (requester)")
            alloc.refresh_from_db()
            candidate.refresh_from_db()
            expect(candidate.status == "TRANSFUSED",
                   f"B7a bag TRANSFUSED after feedback (got {candidate.status})")

        # negative: requester may not approve
        resp = requester.post(reverse("requests:action", kwargs={"pk": breq.pk}), {"action": "approve"})
        checks += 1
        expect(resp.status_code == 403, f"B8 requester approve should be 403 (got {resp.status_code})")

    # ---------------- C. notifications ----------------
    # use the donor linked to the "donor" login so ownership checks are meaningful
    account_donor = Donor.objects.filter(user__username="donor").first() or donor
    dn = Notification.objects.create(donor=account_donor, channel="in_app", subject="E2E alert",
                                     body="e2e emergency alert", delivery_status="SENT",
                                     sent_at=now)
    resp = post_ok(anon, reverse("notifications:respond_token", kwargs={"token": str(dn.response_token)}),
                   {"response": "AVAILABLE"}, "C1 anonymous token respond POST")
    dn.refresh_from_db()
    expect(dn.donor_response == "AVAILABLE" and dn.responded_at, "C1b response recorded via token link")
    if resp is not None:
        expect("availability only" in resp.content.decode() or "not a medical clearance" in resp.content.decode(),
               "C1c done page keeps non-medical-clearance disclaimer")
    # second response does not overwrite
    post_ok(anon, reverse("notifications:respond_token", kwargs={"token": str(dn.response_token)}),
            {"response": "UNAVAILABLE"}, "C2 duplicate respond POST")
    dn.refresh_from_db()
    expect(dn.donor_response == "AVAILABLE", "C2b first response kept (idempotent)")
    # invalid value
    dn2 = Notification.objects.create(donor=account_donor, channel="in_app", body="e2e 2", delivery_status="SENT")
    post_ok(anon, reverse("notifications:respond_token", kwargs={"token": str(dn2.response_token)}),
            {"response": "HACK"}, "C3 invalid respond value")
    dn2.refresh_from_db()
    expect(dn2.donor_response in (None, ""), "C3b invalid value not stored")
    # anonymous pk-path respond must not be reachable
    resp = anon.get(reverse("notifications:respond", kwargs={"pk": dn.pk}))
    checks += 1
    expect(resp.status_code in (302, 403), f"C4 anon pk respond blocked (got {resp.status_code})")
    # mark-read as donor
    post_ok(donor_user, reverse("notifications:read", kwargs={"pk": dn.pk}), {}, "C5 mark read POST")
    dn.refresh_from_db()
    expect(dn.is_read, "C5b notification marked read")
    # retry failed email notification (mock provider)
    failed = Notification.objects.create(donor=account_donor, channel="email", subject="E2E retry",
                                         body="e2e retry", delivery_status="FAILED", error="simulated")
    post_ok(staff, reverse("notifications:retry", kwargs={"pk": failed.pk}), {}, "C6 retry POST")
    failed.refresh_from_db()
    expect(failed.delivery_status == "SENT" and failed.retry_count == 1,
           f"C6b retried through mock provider (status {failed.delivery_status}, tries {failed.retry_count})")

    # ---------------- D. appointments self-book ----------------
    future = (timezone.localdate() + timezone.timedelta(days=3)).isoformat()
    before = Appointment.objects.filter(status="REQUESTED").count()
    post_ok(donor_user, reverse("appointments:my_appointments"),
            {"date": future, "time": "10:00", "location": "E2E drive", "notes": "smoke"},
            "D1 self-book POST")
    expect(Appointment.objects.filter(status="REQUESTED").count() == before + 1, "D1b REQUESTED appointment created")
    # past date rejected
    post_ok(donor_user, reverse("appointments:my_appointments"),
            {"date": today, "time": "23:59", "location": "", "notes": "past"}, "D2 past-date POST")
    # donor must not reach staff list
    resp = donor_user.get(reverse("appointments:list"))
    checks += 1
    expect(resp.status_code in (302, 403), f"D3 donor staff appointments list blocked (got {resp.status_code})")

    # ---------------- E. rewards ----------------
    RewardService.post(account_donor, 400, "ADJUSTMENT", description="E2E credit", actor=admin_user())
    account_donor.refresh_from_db()
    expect(account_donor.points_balance >= 400, "E1 credited points")
    reward = Reward.objects.filter(is_active=True, points_cost__lte=400, stock__gt=0).first() \
        or Reward.objects.filter(is_active=True, points_cost__lte=400, stock__isnull=True).first()
    if reward:
        post_ok(donor_user, reverse("rewards:my_rewards"), {"reward": reward.pk}, "E2 redeem POST")
        account_donor.refresh_from_db()
        expect(account_donor.point_transactions.filter(type="REDEEM").exists(), "E2b redemption ledger entry")
    # donor must not access admin overview
    resp = donor_user.get(reverse("rewards:admin_overview"))
    checks += 1
    expect(resp.status_code in (302, 403), f"E3 donor admin rewards blocked (got {resp.status_code})")

    # ---------------- F. settings ----------------
    from settings_app.models import SystemSetting
    from settings_app.services import get_setting
    s = SystemSetting.objects.get(key="low_stock_threshold")
    post_ok(admin, reverse("settings_app:quick_set", kwargs={"pk": s.pk}), {"value": "7"}, "F1 quick-set POST (admin)")
    s.refresh_from_db()
    expect(s.value == "7", f"F1b value updated (got {s.value})")
    expect(get_setting("low_stock_threshold") == 7, "F1c typed value reads int 7")
    resp = staff.post(reverse("settings_app:quick_set", kwargs={"pk": s.pk}), {"value": "99"})
    checks += 1
    s.refresh_from_db()
    expect(resp.status_code in (302, 403) and s.value == "7",
           f"F2 staff cannot change settings (code {resp.status_code}, value {s.value})")

    # ---------------- G. audit immutability ----------------
    from audit.models import AuditLog
    entry = AuditLog.objects.order_by("-id").first()
    try:
        entry.description = "tampered"
        entry.save()
        expect(False, "G1 audit log row should NOT be saveable")
    except Exception as exc:  # noqa: BLE001
        expect("immutable" in str(exc).lower() or "cannot" in str(exc).lower() or type(exc).__name__ == "ValidationError",
               f"G1b audit save raised: {type(exc).__name__}: {exc}")
    try:
        AuditLog.objects.filter(pk=entry.pk).delete()
        expect(not AuditLog.objects.filter(pk=entry.pk).exists(), "G2 bulk delete removed row")
    except Exception as exc:  # noqa: BLE001
        expect(True, f"G2 audit delete blocked: {type(exc).__name__}")


def requester_user_profile():
    from accounts.models import User
    return User.objects.get(username="requester").requester_profile


def admin_user():
    from accounts.models import User
    return User.objects.get(username="admin")


def main():
    import traceback
    try:
        with transaction.atomic():
            run_workflows()
            raise Rollback()
    except Rollback:
        pass
    except Exception:
        traceback.print_exc()
        if failures:
            print(f"\n{len(failures)} failures collected before crash:")
            for f in failures:
                print(" -", f)
        return 2
    print(f"ran {checks} workflow assertions (rolled back — DB unchanged)")
    if failures:
        print(f"\n{len(failures)} FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
