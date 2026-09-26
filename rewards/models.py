"""Donor rewards: configurable rules, tiers, rewards, and an append-only point ledger."""
from django.conf import settings
from django.db import models


class RewardRule(models.Model):
    """Configurable point values per event. Values are institutional decisions —
    nothing is hard-coded in application logic."""

    class Event(models.TextChoices):
        DONATION_COMPLETED = "DONATION_COMPLETED", "Donation completed"
        EMERGENCY_DONATION = "EMERGENCY_DONATION", "Emergency donation"
        MILESTONE = "MILESTONE", "Milestone donation bonus"
        REFERRAL = "REFERRAL", "Referral bonus"
        EMERGENCY_RESPONSE = "EMERGENCY_RESPONSE", "Emergency call response"
        MANUAL = "MANUAL", "Manual adjustment"

    code = models.SlugField(max_length=60, unique=True)
    event = models.CharField(max_length=20, choices=Event.choices)
    points = models.IntegerField(default=0)
    milestone_every_n = models.PositiveIntegerField(
        null=True, blank=True, help_text="For MILESTONE rules: award bonus every N donations."
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["event"]

    def __str__(self):
        return f"{self.code}: {self.points:+d} pts"


class RewardTier(models.Model):
    """Configurable tiers (e.g. Bronze/Silver/Gold/Platinum) with point thresholds."""

    name = models.CharField(max_length=60, unique=True)
    min_points = models.PositiveIntegerField(default=0)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["min_points"]

    def __str__(self):
        return f"{self.name} (≥ {self.min_points} pts)"


class Reward(models.Model):
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    points_cost = models.PositiveIntegerField()
    stock = models.PositiveIntegerField(null=True, blank=True, help_text="Blank = unlimited")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["points_cost"]

    def __str__(self):
        return f"{self.name} ({self.points_cost} pts)"


class PointTransaction(models.Model):
    """Append-only ledger. Balances are derived from / reconciled against it —
    historical points are never overwritten."""

    class TxnType(models.TextChoices):
        EARN = "EARN", "Earned"
        REDEEM = "REDEEM", "Redemption"
        BONUS = "BONUS", "Bonus"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"

    donor = models.ForeignKey("donors.Donor", on_delete=models.PROTECT, related_name="point_transactions")
    amount = models.IntegerField(help_text="Positive = credit, negative = debit")
    type = models.CharField(max_length=10, choices=TxnType.choices)
    rule = models.ForeignKey(RewardRule, on_delete=models.SET_NULL, null=True, blank=True)
    reward = models.ForeignKey(Reward, on_delete=models.SET_NULL, null=True, blank=True)
    reference = models.CharField(max_length=120, blank=True, db_index=True,
                                 help_text="Idempotency key, e.g. donation:DN-2026-000001")
    description = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    running_balance = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["donor", "reference"])]

    def __str__(self):
        return f"{self.donor.donor_code} {self.amount:+d} ({self.type})"

    def delete(self, *args, **kwargs):
        raise RuntimeError("Point ledger entries cannot be deleted.")


class DonorReward(models.Model):
    class Status(models.TextChoices):
        REDEEMED = "REDEEMED", "Redeemed"
        FULFILLED = "FULFILLED", "Fulfilled"
        CANCELLED = "CANCELLED", "Cancelled"

    donor = models.ForeignKey("donors.Donor", on_delete=models.PROTECT, related_name="rewards")
    reward = models.ForeignKey(Reward, on_delete=models.PROTECT, related_name="redemptions")
    points_spent = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REDEEMED)
    redeemed_at = models.DateTimeField(auto_now_add=True)
    fulfilled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    fulfilled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-redeemed_at"]

    def __str__(self):
        return f"{self.donor.donor_code} redeemed {self.reward.name}"
