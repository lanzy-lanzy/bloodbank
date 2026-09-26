"""Rewards views: donor self-service + admin program management."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.views import View
from django.views.generic import ListView

from audit import services as audit
from core.forms import StyledModelForm
from core.mixins import StaffRequiredMixin
from core.modals import modal_success, render_any
from donors.models import Donor
from rewards.models import DonorReward, PointTransaction, Reward, RewardRule, RewardTier
from rewards.services import RewardError, RewardService


class MyRewardsView(View):
    template_name = "rewards/my_rewards.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.role not in ("DONOR",):
            raise PermissionDenied
        donor = getattr(request.user, "donor_profile", None)
        if donor is None:
            raise PermissionDenied("No donor record is linked to this account.")
        self.donor = donor
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        donor = self.donor
        return render(request, self.template_name, {
            "donor": donor,
            "tier": RewardService.current_tier(donor),
            "next_tier": RewardService.next_tier(donor),
            "tiers": RewardTier.objects.all(),
            "rewards": Reward.objects.filter(is_active=True),
            "redemptions": donor.rewards.select_related("reward").order_by("-redeemed_at")[:20],
            "transactions": donor.point_transactions.order_by("-created_at")[:30],
        })

    def post(self, request):
        reward_id = request.POST.get("reward")
        try:
            donor_reward = RewardService.redeem(self.donor, reward_id, actor=request.user, request=request)
            audit.log(request, action="REWARD_REDEEMED", module="rewards", obj=donor_reward,
                      description=f"Donor {self.donor.donor_code} redeemed '{donor_reward.reward.name}' "
                                  f"for {donor_reward.points_spent} points")
            messages.success(request, f"Redeemed {donor_reward.reward.name}! Staff will arrange fulfillment.")
        except (RewardError, Reward.DoesNotExist) as exc:
            messages.error(request, str(exc) if isinstance(exc, RewardError) else "Reward not available.")
        return modal_success(request, fallback_redirect=reverse("rewards:my_rewards"))


class AdminOverviewView(StaffRequiredMixin, ListView):
    template_name = "rewards/admin_overview.html"
    context_object_name = "donor_rewards"
    paginate_by = 20

    def get_queryset(self):
        return DonorReward.objects.select_related("donor", "reward", "fulfilled_by").order_by("-redeemed_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            "rules": RewardRule.objects.all(),
            "tiers": RewardTier.objects.all(),
            "rewards": Reward.objects.all(),
            "top_donors": Donor.objects.filter(points_balance__gt=0).order_by("-points_balance")[:10],
            "recent_transactions": PointTransaction.objects.select_related("donor", "created_by").order_by("-created_at")[:15],
        })
        return ctx


class RewardRuleForm(StyledModelForm):
    class Meta:
        model = RewardRule
        fields = ["code", "event", "points", "milestone_every_n", "description", "is_active"]


class RewardTierForm(StyledModelForm):
    class Meta:
        model = RewardTier
        fields = ["name", "min_points", "description", "order"]


class RewardForm(StyledModelForm):
    class Meta:
        model = Reward
        fields = ["name", "description", "points_cost", "stock", "is_active"]


class RewardRuleFormView(StaffRequiredMixin, View):
    template_name = "rewards/rule_form.html"

    def get(self, request, pk=None):
        rule = get_object_or_404(RewardRule, pk=pk) if pk else None
        return render_any(request, self.template_name, {"form": RewardRuleForm(instance=rule), "rule": rule}, modal_title='Reward Rule')

    def post(self, request, pk=None):
        rule = get_object_or_404(RewardRule, pk=pk) if pk else None
        form = RewardRuleForm(request.POST, instance=rule)
        if form.is_valid():
            obj = form.save()
            audit.log(request, action="REWARD_RULE_SAVED", module="rewards", obj=obj,
                      description=f"Reward rule '{obj.code}' = {obj.points} pts saved")
            messages.success(request, "Reward rule saved.")
            return modal_success(request, fallback_redirect=reverse("rewards:admin_overview"))
        return render_any(request, self.template_name, {"form": form, "rule": rule}, modal_title="Reward Rule")


class RewardTierFormView(StaffRequiredMixin, View):
    template_name = "rewards/tier_form.html"

    def get(self, request, pk=None):
        tier = get_object_or_404(RewardTier, pk=pk) if pk else None
        return render_any(request, self.template_name, {"form": RewardTierForm(instance=tier), "tier": tier}, modal_title='Reward Tier')

    def post(self, request, pk=None):
        tier = get_object_or_404(RewardTier, pk=pk) if pk else None
        form = RewardTierForm(request.POST, instance=tier)
        if form.is_valid():
            obj = form.save()
            audit.log(request, action="REWARD_TIER_SAVED", module="rewards", obj=obj,
                      description=f"Reward tier '{obj.name}' (≥{obj.min_points}) saved")
            messages.success(request, "Tier saved.")
            return modal_success(request, fallback_redirect=reverse("rewards:admin_overview"))
        return render_any(request, self.template_name, {"form": form, "tier": tier}, modal_title="Reward Tier")


class RewardFormView(StaffRequiredMixin, View):
    template_name = "rewards/reward_form.html"

    def get(self, request, pk=None):
        reward = get_object_or_404(Reward, pk=pk) if pk else None
        return render_any(request, self.template_name, {"form": RewardForm(instance=reward), "reward": reward}, modal_title='Prize Reward')

    def post(self, request, pk=None):
        reward = get_object_or_404(Reward, pk=pk) if pk else None
        form = RewardForm(request.POST, instance=reward)
        if form.is_valid():
            obj = form.save()
            audit.log(request, action="REWARD_SAVED", module="rewards", obj=obj,
                      description=f"Reward '{obj.name}' ({obj.points_cost} pts) saved")
            messages.success(request, "Reward saved.")
            return modal_success(request, fallback_redirect=reverse("rewards:admin_overview"))
        return render_any(request, self.template_name, {"form": form, "reward": reward}, modal_title="Prize Reward")


class RewardFulfillView(StaffRequiredMixin, View):
    def post(self, request, pk):
        donor_reward = get_object_or_404(DonorReward, pk=pk)
        if donor_reward.status == DonorReward.Status.FULFILLED:
            messages.info(request, "Already fulfilled.")
        else:
            RewardService.fulfill(donor_reward, actor=request.user, request=request)
            messages.success(request, "Marked as fulfilled.")
        return modal_success(request, fallback_redirect=reverse("rewards:admin_overview"))


class ManualPointsView(StaffRequiredMixin, View):
    """Manual point adjustment with mandatory reason (audited)."""

    def post(self, request):
        donor = get_object_or_404(Donor, pk=request.POST.get("donor"))
        try:
            amount = int(request.POST.get("amount", "0"))
        except ValueError:
            amount = 0
        reason = request.POST.get("reason", "").strip()
        if amount == 0 or not reason:
            messages.error(request, "A non-zero amount and a reason are required.")
        else:
            try:
                RewardService.post(
                    donor, amount,
                    PointTransaction.TxnType.ADJUSTMENT if amount < 0 else PointTransaction.TxnType.BONUS,
                    reference=f"manual:{request.user.pk}", description=reason, actor=request.user,
                )
                messages.success(request, f"{amount:+d} points applied to {donor.donor_code}.")
            except RewardError as exc:
                messages.error(request, str(exc))
        return modal_success(request, fallback_redirect=request.META.get("HTTP_REFERER", "/rewards/admin/"))
