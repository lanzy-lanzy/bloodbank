"""RewardService — point ledger operations, tiers, milestone bonuses, redemption.

Point values come exclusively from configured RewardRule rows. If a rule is
missing or inactive, no points are awarded (no silent defaults).
"""
import logging
import uuid

from django.db import transaction
from django.db.models import Sum

from rewards.models import DonorReward, PointTransaction, Reward, RewardRule, RewardTier

logger = logging.getLogger("bloodbank.rewards")


class RewardError(Exception):
    pass


class RewardService:
    @staticmethod
    def _ledger_balance(donor):
        return donor.point_transactions.aggregate(total=Sum("amount"))["total"] or 0

    @staticmethod
    def post(donor, amount, txn_type, *, rule=None, reward=None, reference="",
             description="", actor=None):
        """Append a ledger entry and update the cached balance atomically."""
        from audit import services as audit

        with transaction.atomic():
            donor_locked = type(donor).all_objects.select_for_update().get(pk=donor.pk)
            balance = RewardService._ledger_balance(donor_locked) + amount
            if balance < 0:
                raise RewardError(f"Insufficient points: balance {RewardService._ledger_balance(donor_locked)}, attempted {amount}.")
            txn = PointTransaction.objects.create(
                donor=donor_locked, amount=amount, type=txn_type, rule=rule, reward=reward,
                reference=reference, description=description, created_by=actor,
                running_balance=balance,
            )
            donor_locked.points_balance = balance
            donor_locked.save(update_fields=["points_balance"])
            audit.log(None, user=actor, action="REWARD_GRANTED" if amount >= 0 else "POINTS_DEBITED",
                      module="rewards", obj=txn,
                      description=f"{amount:+d} points for donor {donor_locked.donor_code}"
                                  + (f" ({description})" if description else ""))
            return txn

    @staticmethod
    def award_for_event(donor, rule_code, *, reference="", actor=None, request=None, extra=None):
        """Award points for a configured event. Idempotent on `reference`."""
        if reference and PointTransaction.objects.filter(donor=donor, reference=reference).exists():
            logger.info("Skipping duplicate award %s for %s", rule_code, donor.donor_code)
            return None
        rule = RewardRule.objects.filter(code=rule_code, is_active=True).first()
        if rule is None or rule.points == 0:
            return None  # not configured → no award, by design
        return RewardService.post(
            donor, rule.points, PointTransaction.TxnType.EARN, rule=rule,
            reference=reference, description=rule.get_event_display() or rule_code, actor=actor,
        )

    @staticmethod
    def check_milestones(donor, *, actor=None):
        """Award MILESTONE bonus rules based on completed-donation count."""
        awarded = []
        completed = donor.donations.filter(status__in=["COLLECTED", "RELEASED"]).count()
        for rule in RewardRule.objects.filter(event=RewardRule.Event.MILESTONE, is_active=True,
                                              milestone_every_n__isnull=False):
            n = rule.milestone_every_n
            if n and completed > 0 and completed % n == 0:
                reference = f"milestone:{rule.code}:{completed}"
                txn = RewardService.award_for_event(
                    donor, rule.code, reference=reference, actor=actor,
                )
                if txn:
                    awarded.append(txn)
        return awarded

    @staticmethod
    def current_tier(donor):
        tier = None
        for candidate in RewardTier.objects.order_by("min_points"):
            if donor.points_balance >= candidate.min_points:
                tier = candidate
        return tier

    @staticmethod
    def next_tier(donor):
        current = RewardService.current_tier(donor)
        qs = RewardTier.objects.filter(min_points__gt=donor.points_balance).order_by("min_points")
        return qs.first()

    @staticmethod
    def redeem(donor, reward_id, *, actor=None, request=None):
        """Redeem a reward: debit ledger, decrement stock, create DonorReward."""
        with transaction.atomic():
            reward = Reward.objects.select_for_update().get(pk=reward_id, is_active=True)
            if reward.stock is not None:
                if reward.stock < 1:
                    raise RewardError(f"'{reward.name}' is out of stock.")
            if donor.points_balance < reward.points_cost:
                raise RewardError(
                    f"Insufficient points: {donor.points_balance} available, {reward.points_cost} required."
                )
            RewardService.post(
                donor, -reward.points_cost, PointTransaction.TxnType.REDEEM, reward=reward,
                reference=f"redeem:{reward.pk}:{uuid.uuid4().hex[:12]}",
                description=f"Redeemed {reward.name}", actor=actor,
            )
            if reward.stock is not None:
                reward.stock -= 1
                reward.save(update_fields=["stock"])
            donor_reward = DonorReward.objects.create(
                donor=donor, reward=reward, points_spent=reward.points_cost,
            )
        return donor_reward

    @staticmethod
    def fulfill(donor_reward, *, actor=None, request=None):
        from audit import services as audit
        from django.utils import timezone

        with transaction.atomic():
            donor_reward.status = DonorReward.Status.FULFILLED
            donor_reward.fulfilled_by = actor
            donor_reward.fulfilled_at = timezone.now()
            donor_reward.save(update_fields=["status", "fulfilled_by", "fulfilled_at"])
            audit.log(request, user=actor, action="REWARD_FULFILLED", module="rewards", obj=donor_reward,
                      description=f"Reward '{donor_reward.reward.name}' fulfilled for donor "
                                  f"{donor_reward.donor.donor_code}")
        return donor_reward

    @staticmethod
    def reconcile_balance(donor):
        """Recompute cached balance from the ledger (used by maintenance command)."""
        balance = RewardService._ledger_balance(donor)
        if donor.points_balance != balance:
            logger.warning("Reconciling donor %s cached balance %s → %s",
                           donor.donor_code, donor.points_balance, balance)
            donor.points_balance = balance
            donor.save(update_fields=["points_balance"])
        return balance
