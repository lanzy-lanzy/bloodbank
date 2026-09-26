"""Notification views: inbox, badge, donor responses (token links), template admin."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.views import View
from django.views.generic import ListView

from audit import services as audit
from core.forms import StyledModelForm
from core.mixins import AdminRequiredMixin, StaffRequiredMixin, is_htmx
from core.modals import modal_success, render_any
from notifications.models import KNOWN_VARIABLES, Notification, NotificationTemplate
from notifications.services import NotificationError, NotificationService


class InboxView(View):
    """In-app inbox for the signed-in user; donors also see donor-targeted messages."""

    template_name = "notifications/inbox.html"

    def get(self, request):
        donor = getattr(request.user, "donor_profile", None)
        qs = Notification.objects.filter(channel="in_app").filter(
            Q(user=request.user) | (Q(donor=donor) if donor else Q(pk__in=[]))
        ).select_related("donor", "blood_request", "template").order_by("-created_at")
        unread = qs.filter(is_read=False).count()
        qs = qs[:100]
        return render(request, self.template_name, {"notifications": qs, "unread": unread})


class InboxBadgeView(View):
    def get(self, request):
        donor = getattr(request.user, "donor_profile", None)
        count = Notification.objects.filter(channel="in_app", is_read=False).filter(
            Q(user=request.user) | (Q(donor=donor) if donor else Q(pk__in=[]))
        ).count()
        return render(request, "notifications/_badge.html", {"count": count})


class NotificationReadView(View):
    def post(self, request, pk):
        donor = getattr(request.user, "donor_profile", None)
        notification = get_object_or_404(Notification, pk=pk)
        owns = notification.user_id == request.user.pk or (donor and notification.donor_id == donor.pk) \
            or request.user.role in ("ADMIN", "STAFF")
        if not owns:
            raise PermissionDenied
        from django.utils import timezone
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save(update_fields=["is_read", "read_at"])
        return redirect("notifications:inbox")


class NotificationRespondView(View):
    """Donor response to an emergency alert.

    Works for signed-in donors (own notifications only) and via tokenized links
    (public path — token is the capability). A response is NEVER treated as a
    medical eligibility confirmation; the message says so explicitly.
    """

    template_name = "notifications/respond.html"

    def _resolve(self, request, pk=None, token=None):
        if token:
            return get_object_or_404(Notification, response_token=token)
        notification = get_object_or_404(Notification, pk=pk)
        donor = getattr(request.user, "donor_profile", None)
        if notification.donor_id and donor and notification.donor_id == donor.pk:
            return notification
        if request.user.role in ("ADMIN", "STAFF"):
            return notification
        raise PermissionDenied("This notification belongs to another donor.")

    def get(self, request, pk=None, token=None):
        notification = self._resolve(request, pk, token)
        standalone = token is not None and not request.user.is_authenticated
        return render(request, self.template_name, {
            "notification": notification,
            "choices": Notification.DonorResponse.choices,
            "standalone": standalone,
            "base_template": "base_auth.html" if standalone else "base.html",
        })

    def post(self, request, pk=None, token=None):
        notification = self._resolve(request, pk, token)
        value = request.POST.get("response", "")
        valid = [c[0] for c in Notification.DonorResponse.choices]
        if value not in valid:
            messages.error(request, "Choose one of the response options.")
            return redirect(request.path)
        if notification.donor_response:
            messages.info(request, "You already responded to this alert. Staff will follow up if needed.")
        else:
            NotificationService.record_response(notification, value)
            messages.success(request, "Response recorded. Note: you will still undergo standard screening "
                                      "before donating — this response is not a medical clearance.")
        if token:
            standalone = not request.user.is_authenticated
            return render(request, "notifications/respond_done.html", {
                "notification": notification,
                "standalone": standalone,
                "base_template": "base_auth.html" if standalone else "base.html",
            })
        return redirect("notifications:inbox")


class NotificationRetryView(StaffRequiredMixin, View):
    def post(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk)
        try:
            NotificationService.retry_failed(notification)
            if notification.delivery_status == Notification.DeliveryStatus.SENT:
                messages.success(request, "Notification resent.")
            else:
                messages.error(request, f"Resend failed: {notification.error}")
        except NotificationError as exc:
            messages.error(request, str(exc))
        return modal_success(request, fallback_redirect=request.META.get("HTTP_REFERER", "/notifications/"))


class DeliveryListView(StaffRequiredMixin, ListView):
    template_name = "notifications/delivery_list.html"
    context_object_name = "notifications"
    paginate_by = 30

    def get_queryset(self):
        qs = Notification.objects.select_related("donor", "user", "template", "blood_request")
        status = self.request.GET.get("status", "")
        channel = self.request.GET.get("channel", "")
        if status:
            qs = qs.filter(delivery_status=status)
        if channel:
            qs = qs.filter(channel=channel)
        self.status, self.channel = status, channel
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            "status_filter": self.status, "channel_filter": self.channel,
            "statuses": Notification.DeliveryStatus.choices,
            "channels": Notification.Channel.choices,
        })
        return ctx


# --- Template administration -----------------------------------------------------
class TemplateForm(StyledModelForm):
    class Meta:
        model = NotificationTemplate
        fields = ["name", "code", "subject", "body", "channels", "description", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from notifications.models import KNOWN_VARIABLES
        self.fields["body"].help_text = ("Variables: " + ", ".join(f"{{{{ {v} }}}}" for v in KNOWN_VARIABLES))


class TemplateListView(AdminRequiredMixin, ListView):
    template_name = "notifications/template_list.html"
    context_object_name = "templates"

    def get_queryset(self):
        return NotificationTemplate.objects.all()


class TemplateFormView(AdminRequiredMixin, View):
    template_name = "notifications/template_form.html"

    def get(self, request, pk=None):
        template = get_object_or_404(NotificationTemplate, pk=pk) if pk else None
        return render_any(request, self.template_name, {
            "form": TemplateForm(instance=template), "ntemplate": template,
            "known_variables": KNOWN_VARIABLES,
            "recent_notifications": (Notification.objects.filter(template=template)
                                     .order_by("-created_at")[:8] if template else []),
            "modal_maxw": "max-w-4xl",
        }, modal_title=("Edit template: " + template.code) if template else "New Notification Template")

    def post(self, request, pk=None):
        template = get_object_or_404(NotificationTemplate, pk=pk) if pk else None
        form = TemplateForm(request.POST, instance=template)
        if form.is_valid():
            obj = form.save()
            audit.log(request, action="NOTIFICATION_TEMPLATE_SAVED", module="notifications", obj=obj,
                      description=f"Notification template '{obj.code}' saved")
            messages.success(request, "Template saved.")
            return modal_success(request, fallback_redirect=reverse("notifications:template_list"))
        return render_any(request, self.template_name, {
            "form": form, "ntemplate": template, "known_variables": KNOWN_VARIABLES,
            "recent_notifications": (Notification.objects.filter(template=template)
                                     .order_by("-created_at")[:8] if template else []),
            "modal_maxw": "max-w-4xl",
        }, modal_title="Notification Template")
