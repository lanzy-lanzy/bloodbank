from django.urls import path

from core import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),                      # public landing page
    path("dashboard/", views.dashboard, name="dashboard"),  # role dashboard (login target)
    path("dashboard/charts/<str:chart>/", views.chart_data, name="chart_data"),
]
