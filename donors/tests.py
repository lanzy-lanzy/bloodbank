"""Donor domain: eligibility engine (fail-safe), soft deletion, data scoping."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from core.testing import (make_blood_type, make_component, make_donor, make_donation,
                          make_bag, make_user, set_rules)
from donors.models import Donor
from donors.services import DonorEligibilityService


class EligibilityRuleTests(TestCase):
    def setUp(self):
        self.bt = make_blood_type("O", "POS")
        self.donor = make_donor(blood_type=self.bt)

    def test_eligible_when_rules_configured_and_satisfied(self):
        set_rules(min_age_years=18, max_age_years=65, min_donation_interval_days=60)
        result = DonorEligibilityService.evaluate(self.donor)
        self.assertEqual(result.status, "ELIGIBLE")
        self.assertTrue(result.is_eligible)

    def test_interval_not_configured_fails_safe_to_staff_review(self):
        # No min_donation_interval_days setting at all + a past donation.
        make_donation(self.donor, status="RELEASED",
                      donation_date=timezone.localdate() - timedelta(days=400))
        result = DonorEligibilityService.evaluate(self.donor)
        self.assertEqual(result.status, "REQUIRES_STAFF_REVIEW")

    def test_recent_donation_defers_until_interval(self):
        set_rules(min_age_years=18, max_age_years=65, min_donation_interval_days=60)
        last = timezone.localdate() - timedelta(days=10)
        make_donation(self.donor, status="RELEASED", donation_date=last)
        result = DonorEligibilityService.evaluate(self.donor)
        self.assertEqual(result.status, "DEFERRED")
        self.assertEqual(result.next_eligible_date, last + timedelta(days=60))

    def test_perm_deferred_never_eligible(self):
        donor = make_donor(blood_type=self.bt, status=Donor.Status.PERM_DEFERRED,
                           deferral_reason="test")
        set_rules(min_age_years=18, max_age_years=65, min_donation_interval_days=60)
        result = DonorEligibilityService.evaluate(donor)
        self.assertEqual(result.status, "NOT_ELIGIBLE")

    def test_active_temp_deferred_within_window(self):
        donor = make_donor(blood_type=self.bt, status=Donor.Status.TEMP_DEFERRED,
                           deferred_until=timezone.localdate() + timedelta(days=30))
        set_rules(min_age_years=18, max_age_years=65, min_donation_interval_days=60)
        result = DonorEligibilityService.evaluate(donor)
        self.assertEqual(result.status, "DEFERRED")

    def test_rolling_year_cap(self):
        set_rules(min_age_years=18, max_age_years=65, min_donation_interval_days=1,
                  max_donations_per_year=2)
        for days_back in (5, 10, 15):
            make_donation(self.donor, status="COLLECTED",
                          donation_date=timezone.localdate() - timedelta(days=days_back))
        result = DonorEligibilityService.evaluate(self.donor)
        self.assertEqual(result.status, "NOT_ELIGIBLE")

    def test_age_bounds_use_configured_values(self):
        set_rules(min_age_years=18, max_age_years=65, min_donation_interval_days=60)
        young = make_donor(blood_type=self.bt, age=16)
        self.assertEqual(DonorEligibilityService.evaluate(young).status, "NOT_ELIGIBLE")


class DonorRepositoryTests(TestCase):
    def test_soft_delete_hides_from_default_manager(self):
        donor = make_donor()
        donor.soft_delete()
        self.assertNotIn(donor, Donor.objects.all())
        self.assertIn(donor, Donor.all_objects.all())
        self.assertTrue(donor.is_deleted and donor.deleted_at)

    def test_codes_auto_assigned(self):
        donor = make_donor()
        self.assertTrue(donor.donor_code.startswith("DON-"))


class DonorAccessControlTests(TestCase):
    def setUp(self):
        from django.test import Client
        self.client = Client()
        self.bt = make_blood_type("A", "POS")
        self.staff = make_user("sec-staff", role="STAFF")
        donor_user = make_user("sec-donor", role="DONOR")
        self.my_donor = make_donor(user=donor_user, blood_type=self.bt)
        self.other_donor = make_donor(blood_type=self.bt)

    def test_donor_cannot_see_other_donor(self):
        self.client.login(username="sec-donor", password="Test12345!")
        resp = self.client.get(f"/donors/{self.other_donor.pk}/")
        self.assertIn(resp.status_code, (403, 302, 404))

    def test_donor_cannot_open_staff_directory(self):
        self.client.login(username="sec-donor", password="Test12345!")
        resp = self.client.get("/donors/")
        self.assertIn(resp.status_code, (403, 302))

    def test_staff_can_open_directory(self):
        self.client.login(username="sec-staff", password="Test12345!")
        resp = self.client.get("/donors/")
        self.assertEqual(resp.status_code, 200)
