"""Shared object factories for the test suites.

These build minimal, valid instances directly through the ORM so each test
can state exactly which fixture it depends on. They never touch the
development database — Django's test runner works on a throw-away database.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

PASSWORD = "Test12345!"


def make_user(username, role="STAFF", **extra):
    User = get_user_model()
    return User.objects.create_user(username=username, password=PASSWORD, role=role, **extra)


def make_blood_type(abo="O", rh="POS"):
    from inventory.models import BloodType
    bt, _ = BloodType.objects.get_or_create(abo=abo, rh=rh, defaults={"is_active": True})
    return bt


def make_component(code="wb", name=None, shelf_life=21):
    from inventory.models import BloodComponent
    comp, _ = BloodComponent.objects.get_or_create(
        code=code, defaults={"name": name or code.upper(), "default_shelf_life_days": shelf_life})
    return comp


def make_test_type(name="E2E Test", category="INFECTIOUS", is_required=True, is_active=True):
    from inventory.models import TestType
    return TestType.objects.create(name=name, category=category,
                                   is_required=is_required, is_active=is_active)


def make_compat_rule(patient, donor_type, component=None, allowed=True, approved=True, active=True):
    from inventory.models import CompatibilityRule
    rule = CompatibilityRule.objects.create(
        patient_type=patient, donor_type=donor_type, component=component,
        is_allowed=allowed, is_active=active, note="test fixture")
    if approved:
        rule.approved_by = make_user(f"approver-{rule.pk}", role="ADMIN")
        rule.approved_at = timezone.now()
        rule.save(update_fields=["approved_by", "approved_at"])
    return rule


def make_donor(blood_type=None, status="ACTIVE", age=30, **extra):
    from donors.models import Donor
    dob = timezone.localdate() - timedelta(days=int(age * 365.25))
    return Donor.objects.create(
        first_name=extra.pop("first_name", "Test"),
        last_name=extra.pop("last_name", "Donor"),
        date_of_birth=dob, sex="MALE", contact_number="0900-000-0000",
        blood_type=blood_type or make_blood_type(), status=status, **extra)


def make_donation(donor, status="REGISTERED", **extra):
    from donations.models import Donation
    return Donation.objects.create(donor=donor, status=status,
                                   blood_type=extra.pop("blood_type", donor.blood_type), **extra)


def make_bag(donor=None, blood_type=None, component=None, status="QUARANTINED",
             expires_in_days=None, collected_at=None, **extra):
    from inventory.models import BloodBag
    component = component or make_component()
    collected_at = collected_at or timezone.now()
    if expires_in_days is None:
        expires_in_days = component.default_shelf_life_days
    return BloodBag.objects.create(
        donor=donor, blood_type=blood_type or (donor and donor.blood_type) or make_blood_type(),
        component=component, collected_at=collected_at,
        expires_at=collected_at + timedelta(days=expires_in_days),
        volume_ml=450, status=status, **extra)


def make_org(name="Test Hospital"):
    from requests.models import Organization
    return Organization.objects.create(name=name, org_type="HOSPITAL")


def make_requester(username="req1", org=None):
    from requests.models import RequesterProfile
    user = make_user(username, role="REQUESTER")
    org = org or make_org(f"{username} org")
    RequesterProfile.objects.create(user=user, organization=org)
    user.refresh_from_db()
    return user


def make_request(organization, created_by=None, status="SUBMITTED", urgency="ROUTINE", **extra):
    from requests.models import BloodRequest
    return BloodRequest.objects.create(
        organization=organization, created_by=created_by, status=status, urgency=urgency,
        patient_reference=extra.pop("patient_reference", "PT-TEST-1"),
        required_by=timezone.now() + timedelta(days=1), **extra)


def make_item(blood_request, blood_type, component=None, quantity=1):
    from requests.models import RequestItem
    return RequestItem.objects.create(request=blood_request, blood_type=blood_type,
                                      component=component or make_component(), quantity=quantity)


def set_rules(**values):
    """Convenience: write eligibility/business settings as ints."""
    from settings_app.services import set_setting
    for key, val in values.items():
        set_setting(key, val, value_type="int" if isinstance(val, int) else "str")
