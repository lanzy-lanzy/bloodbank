"""URL configuration for the Blood Bank Management System."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from accounts import views as accounts_views

# Public self-registration lives at the root path /register/ (allow-listed
# exactly in core/middleware.py) but is implemented in the accounts app.
public_registration_patterns = ([
    path("", accounts_views.RegisterView.as_view(), name="register"),
    path("done/", accounts_views.register_done, name="register_done"),
], "public_registration")

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("register/", include(public_registration_patterns)),
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
