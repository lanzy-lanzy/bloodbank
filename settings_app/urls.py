from django.urls import path

from settings_app import views

app_name = "settings_app"

urlpatterns = [
    path("", views.SettingsIndexView.as_view(), name="index"),
    path("create/", views.SettingSaveView.as_view(), name="create"),
    path("<int:pk>/save/", views.SettingSaveView.as_view(), name="save"),
    path("<int:pk>/quick/", views.QuickSetView.as_view(), name="quick_set"),
    path("<int:pk>/delete/", views.SettingDeleteView.as_view(), name="delete"),
    path("blood-bank/", views.BloodBankConfigView.as_view(), name="blood_bank"),
    path("blood-bank/types/create/", views.BloodTypeManageView.as_view(), name="blood_type_create"),
    path("blood-bank/types/<int:pk>/", views.BloodTypeManageView.as_view(), name="blood_type_edit"),
    path("blood-bank/components/create/", views.ComponentManageView.as_view(), name="component_create"),
    path("blood-bank/components/<int:pk>/", views.ComponentManageView.as_view(), name="component_edit"),
    path("blood-bank/tests/create/", views.TestTypeManageView.as_view(), name="test_type_create"),
    path("blood-bank/tests/<int:pk>/", views.TestTypeManageView.as_view(), name="test_type_edit"),
    path("blood-bank/compatibility/create/", views.CompatibilityManageView.as_view(), name="compatibility_create"),
    path("blood-bank/compatibility/<int:pk>/", views.CompatibilityManageView.as_view(), name="compatibility_edit"),
    path("blood-bank/compatibility/<int:pk>/delete/", views.CompatibilityDeleteView.as_view(), name="compatibility_delete"),
]
