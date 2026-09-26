"""Rewards: ledger integrity, tiers, redemption guards."""
from django.test import TestCase
from django.urls import reverse

from core.testing import make_donor, make_user
from rewards.models import PointTransaction, Reward, RewardRule, RewardTier
from rewards.services import RewardError, RewardService


class LedgerTests(TestCase):
    def setUp(self):
        self.donor = make_donor()
        self.staff = make_user("rw-staff", role="STAFF")

    def test_post_updates_balance_and_running_total(self):
        RewardService.post(self.donor, 100, "EARN", description="first", actor=self.staff)
        RewardService.post(self.donor, 50, "BONUS", description="second", actor=self.staff)
        self.donor.refresh_from_db()
        txns = list(self.donor.point_transactions.order_by("id"))
        self.assertEqual([t.running_balance for t in txns], [100, 150])
        self.assertEqual(self.donor.points_balance, 150)

    def test_negative_below_zero_refused(self):
        RewardService.post(self.donor, 30, "EARN", actor=self.staff)
        with self.assertRaises(RewardError):
            RewardService.post(self.donor, -31, "REDEEM", actor=self.staff)
        self.donor.refresh_from_db()
        self.assertEqual(self.donor.points_balance, 30)

    def test_award_for_event_uses_configured_rule_only(self):
        # no rule configured -> no award, silently (by design)
        self.assertIsNone(RewardService.award_for_event(self.donor, "DONATION_COMPLETED"))
        RewardRule.objects.create(code="DONATION_COMPLETED", event="DONATION_COMPLETED", points=200)
        RewardService.award_for_event(self.donor, "DONATION_COMPLETED", reference="dn:1")
        self.donor.refresh_from_db()
        self.assertEqual(self.donor.points_balance, 200)

    def test_award_idempotent_per_reference(self):
        RewardRule.objects.create(code="DONATION_COMPLETED", event="DONATION_COMPLETED", points=200)
        RewardService.award_for_event(self.donor, "DONATION_COMPLETED", reference="dn:2")
        RewardService.award_for_event(self.donor, "DONATION_COMPLETED", reference="dn:2")
        self.donor.refresh_from_db()
        self.assertEqual(self.donor.points_balance, 200)


class TierTests(TestCase):
    def setUp(self):
        RewardTier.objects.create(name="Bronze", min_points=0, order=1)
        RewardTier.objects.create(name="Silver", min_points=300, order=2)
        RewardTier.objects.create(name="Gold", min_points=1000, order=3)

    def test_tier_progression(self):
        donor = make_donor()
        self.assertEqual(RewardService.current_tier(donor).name, "Bronze")
        donor.points_balance = 350
        self.assertEqual(RewardService.next_tier(donor).name, "Gold")


class RedemptionTests(TestCase):
    def setUp(self):
        self.donor = make_donor()
        self.reward = Reward.objects.create(name="T-shirt", points_cost=100, stock=2)

    def test_insufficient_points(self):
        with self.assertRaises(RewardError):
            RewardService.redeem(self.donor, self.reward.pk)

    def test_redeem_debits_and_decrements_stock(self):
        RewardService.post(self.donor, 250, "EARN", actor=make_user("rw-adm", role="ADMIN"))
        self.donor.refresh_from_db()  # balance is cached on the row; instance must reload
        RewardService.redeem(self.donor, self.reward.pk)
        self.donor.refresh_from_db()
        self.reward.refresh_from_db()
        self.assertEqual(self.donor.points_balance, 150)
        self.assertEqual(self.reward.stock, 1)
        self.assertTrue(self.donor.point_transactions.filter(
            type=PointTransaction.TxnType.REDEEM).exists())

    def test_out_of_stock(self):
        self.reward.stock = 0
        self.reward.save()
        RewardService.post(self.donor, 250, "EARN")
        with self.assertRaises(RewardError):
            RewardService.redeem(self.donor, self.reward.pk)


class RewardsAccessTests(TestCase):
    def test_donor_blocked_from_admin_overview(self):
        u = make_user("rw-donor", role="DONOR")
        make_donor(user=u)
        self.client.login(username="rw-donor", password="Test12345!")
        resp = self.client.get(reverse("rewards:admin_overview"))
        self.assertIn(resp.status_code, (403, 302))
