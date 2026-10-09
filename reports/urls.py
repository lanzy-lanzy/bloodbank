from django.urls import path

from reports import views

app_name = "reports"

urlpatterns = [
    path("", views.ReportCenterView.as_view(), name="center"),
    path("<str:key>/", views.ReportRunView.as_view(), name="run"),
    # Print preview and PDF of exactly what the run view shows, same filters.
    # Declared before the .csv route is irrelevant (distinct literals) but kept
    # next to it so the three export paths read together.
    path("<str:key>/document/", views.ReportDocumentView.as_view(), name="document"),
    path("<str:key>/export.pdf", views.ReportPdfView.as_view(), name="pdf"),
    path("<str:key>/export.csv", views.ReportExportView.as_view(), name="export"),
]
