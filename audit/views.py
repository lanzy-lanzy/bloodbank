from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q
from django.views.generic import ListView

from audit.models import AuditLog
from core.mixins import AdminRequiredMixin, StaffRequiredMixin


class AuditListView(StaffRequiredMixin, ListView):
    """Admin: full audit log. Staff: limited to operational modules."""

    template_name = "audit/list.html"
    context_object_name = "logs"
    paginate_by = 30

    STAFF_VISIBLE_MODULES = {
        "donors", "donations", "inventory", "requests", "appointments",
        "notifications", "rewards",
    }

    def get_queryset(self):
        qs = AuditLog.objects.select_related("user")
        if self.request.user.role == "STAFF":
            qs = qs.filter(module__in=self.STAFF_VISIBLE_MODULES)
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(action__icontains=q) | Q(description__icontains=q)
                | Q(object_type__icontains=q) | Q(object_id__icontains=q)
                | Q(user__username__icontains=q)
            )
        for key, field in (("module", "module"), ("action", "action"),
                           ("object_type", "object_type"), ("user", "user_id")):
            value = self.request.GET.get(key)
            if value:
                qs = qs.filter(**{field: value})
        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["modules"] = sorted(AuditLog.objects.values_list("module", flat=True).distinct())
        ctx["actions"] = sorted(AuditLog.objects.values_list("action", flat=True).distinct())
        ctx["q"] = self.request.GET.get("q", "")
        return ctx


class AuditDetailFragmentView(AdminRequiredMixin, ListView):
    template_name = "audit/_detail.html"
    context_object_name = "logs"

    def get_queryset(self):
        return AuditLog.objects.filter(
            object_type=self.kwargs["object_type"], object_id=self.kwargs["object_id"]
        ).select_related("user").order_by("-created_at")[:50]
