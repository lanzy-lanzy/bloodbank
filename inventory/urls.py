from django.urls import path

from inventory import views

app_name = "inventory"

urlpatterns = [
    path("", views.InventoryDashboardView.as_view(), name="dashboard"),
    path("bags/", views.BagListView.as_view(), name="bag_list"),
    path("bags/register/", views.BagRegisterView.as_view(), name="bag_register"),
    path("bags/<int:pk>/", views.BagDetailView.as_view(), name="bag_detail"),
    path("bags/<int:pk>/test/", views.BagTestResultView.as_view(), name="bag_test"),
    path("bags/<int:pk>/test/<int:result_pk>/verify/", views.TestVerifyView.as_view(), name="test_verify"),
    path("bags/<int:pk>/release/", views.BagReleaseView.as_view(), name="bag_release"),
    path("bags/<int:pk>/transition/", views.BagTransitionView.as_view(), name="bag_transition"),
    path("transactions/", views.InventoryTransactionListView.as_view(), name="transactions"),
    path("compatibility/", views.CompatCheckView.as_view(), name="compat_check"),
]
