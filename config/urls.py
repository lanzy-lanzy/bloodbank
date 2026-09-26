"""URL configuration for the Blood Bank Management System."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", include("core.urls")),
    path("accounts/", include("accounts.urls")),
    path("audit/", include("audit.urls")),
    path("donors/", include("donors.urls")),
    path("appointments/", include("appointments.urls")),
    path("donations/", include("donations.urls")),
    path("inventory/", include("inventory.urls")),
    path("requests/", include("requests.urls")),
    path("notifications/", include("notifications.urls")),
    path("rewards/", include("rewards.urls")),
    path("reports/", include("reports.urls")),
    path("settings/", include("settings_app.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler400 = "core.views.handler400"
handler403 = "core.views.handler403"
handler404 = "core.views.handler404"
handler500 = "core.views.handler500"
