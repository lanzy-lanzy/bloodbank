from django.urls import path

from appointments import views

app_name = "appointments"

urlpatterns = [
    path("", views.AppointmentListView.as_view(), name="list"),
    path("badge/", views.AppointmentBadgeView.as_view(), name="badge"),
    path("create/", views.AppointmentCreateView.as_view(), name="create"),
    path("schedule-all/", views.AppointmentBroadcastCreateView.as_view(), name="broadcast_schedule"),
    path("<int:pk>/edit/", views.AppointmentUpdateView.as_view(), name="edit"),
    path("<int:pk>/status/", views.AppointmentStatusView.as_view(), name="status"),
    path("my/", views.MyAppointmentsView.as_view(), name="my_appointments"),
    path("my/badge/", views.MyAppointmentsBadgeView.as_view(), name="my_badge"),
]
