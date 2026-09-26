from django.contrib import admin
from django.urls import path

from audit import views

app_name = "audit"

urlpatterns = [
    path("", views.AuditListView.as_view(), name="list"),
    path("object/<str:object_type>/<str:object_id>/",
         views.AuditDetailFragmentView.as_view(), name="object_history"),
]


@admin.register(views.AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only admin — audit records can never be edited or deleted here."""

    list_display = ("created_at", "action", "module", "object_type", "object_id", "user")
    list_filter = ("module", "action")
    search_fields = ("action", "description", "object_id", "user__username")
    readonly_fields = [f.name for f in views.AuditLog._meta.fields]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
