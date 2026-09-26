from django.urls import path

from reports import views

app_name = "reports"

urlpatterns = [
    path("", views.ReportCenterView.as_view(), name="center"),
    path("<str:key>/", views.ReportRunView.as_view(), name="run"),
    path("<str:key>/export.csv", views.ReportExportView.as_view(), name="export"),
]
