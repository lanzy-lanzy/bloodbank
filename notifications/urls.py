from django.urls import path

from notifications import views

app_name = "notifications"

urlpatterns = [
    path("", views.InboxView.as_view(), name="inbox"),
    path("badge/", views.InboxBadgeView.as_view(), name="inbox_badge"),
    path("<int:pk>/read/", views.NotificationReadView.as_view(), name="read"),
    path("<int:pk>/respond/", views.NotificationRespondView.as_view(), name="respond"),
    path("<int:pk>/retry/", views.NotificationRetryView.as_view(), name="retry"),
    path("respond/<uuid:token>/", views.NotificationRespondView.as_view(), name="respond_token"),
    path("delivery/", views.DeliveryListView.as_view(), name="delivery_list"),
    path("templates/", views.TemplateListView.as_view(), name="template_list"),
    path("templates/create/", views.TemplateFormView.as_view(), name="template_create"),
    path("templates/<int:pk>/edit/", views.TemplateFormView.as_view(), name="template_edit"),
]
