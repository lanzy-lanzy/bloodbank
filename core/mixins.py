"""Role-based access-control mixins used by all module views.

Roles are enforced here AND by Django permissions; object-level checks live in
the individual views/services (never rely on the UI hiding a link).
"""
from django.contrib.auth.mixins import AccessMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied


def is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


class RoleRequiredMixin(AccessMixin):
    """Allow access only to the listed roles (accounts.User.role values)."""

    roles: list[str] = []

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if self.roles and request.user.role not in self.roles:
            raise PermissionDenied("You do not have permission to access this page.")
        return super().dispatch(request, *args, **kwargs)


class AdminRequiredMixin(RoleRequiredMixin):
    roles = ["ADMIN"]


class StaffRequiredMixin(RoleRequiredMixin):
    """ADMIN also passes: admins have full access to every operational module."""

    roles = ["ADMIN", "STAFF"]


class DonorRequiredMixin(RoleRequiredMixin):
    roles = ["ADMIN", "STAFF", "DONOR"]


class RequesterRequiredMixin(RoleRequiredMixin):
    roles = ["ADMIN", "STAFF", "REQUESTER"]


class PermissionRequiredMixin(UserPassesTestMixin):
    """Thin wrapper for explicit Django-permission checks."""

    permission: str = ""

    def test_func(self):
        return self.request.user.has_perm(self.permission)
