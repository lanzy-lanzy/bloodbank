from django.urls import path

from rewards import views

app_name = "rewards"

urlpatterns = [
    path("my/", views.MyRewardsView.as_view(), name="my_rewards"),
    path("admin/", views.AdminOverviewView.as_view(), name="admin_overview"),
    path("admin/rules/create/", views.RewardRuleFormView.as_view(), name="rule_create"),
    path("admin/rules/<int:pk>/edit/", views.RewardRuleFormView.as_view(), name="rule_edit"),
    path("admin/tiers/create/", views.RewardTierFormView.as_view(), name="tier_create"),
    path("admin/tiers/<int:pk>/edit/", views.RewardTierFormView.as_view(), name="tier_edit"),
    path("admin/rewards/create/", views.RewardFormView.as_view(), name="reward_create"),
    path("admin/rewards/<int:pk>/edit/", views.RewardFormView.as_view(), name="reward_edit"),
    path("admin/donor-rewards/<int:pk>/fulfill/", views.RewardFulfillView.as_view(), name="fulfill"),
    path("admin/manual-points/", views.ManualPointsView.as_view(), name="manual_points"),
]
