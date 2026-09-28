"""Blood request views.

Access rules (object-level):
* Requesters see ONLY their own organization's requests.
* Staff/admin see all and manage the workflow.
* Donors have no access.
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from audit import services as audit
from core.mixins import StaffRequiredMixin, is_htmx
from core.modals import modal_success, render_any
from requests.forms import (
    AllocateForm,
    BloodRequestForm,
    OrganizationForm,
    RejectForm,
    RequestItemAddForm,
)
from requests.models import Allocation, BloodRequest, Organization, RequestItem
from requests.services import BloodRequestService, RequestError


def _visible_requests(user):
    if user.role in ("ADMIN", "STAFF"):
        return BloodRequest.objects.all()
    profile = getattr(user, "requester_profile", None)
    if profile is None:
        return BloodRequest.objects.none()
    # Walk-in records are the blood bank's own; they belong to the desk
    # organization only administratively and must never surface for a
    # requester account that happens to sit on that organization.
    return BloodRequest.objects.filter(organization=profile.organization).exclude(
        channel=BloodRequest.Channel.WALK_IN)


def _get_request_or_403(user, pk):
    blood_request = get_object_or_404(BloodRequest.objects.select_related("organization", "created_by"), pk=pk)
    if user.role == "REQUESTER":
        profile = getattr(user, "requester_profile", None)
        if blood_request.is_walk_in:
            raise PermissionDenied("This request is a blood bank walk-in record.")
        if profile is None or profile.organization_id != blood_request.organization_id:
            raise PermissionDenied("You can only access your own organization's requests.")
    elif user.role not in ("ADMIN", "STAFF"):
        raise PermissionDenied
    return blood_request


class RequestListView(View):
    template_name = "requests/list.html"
    paginate_by = 15

    def dispatch(self, request, *args, **kwargs):
        if request.user.role not in ("ADMIN", "STAFF", "REQUESTER"):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        from django.core.paginator import Paginator

        qs = _visible_requests(request.user).select_related("organization", "created_by")
        q = request.GET.get("q", "").strip()
        status = request.GET.get("status", "")
        urgency = request.GET.get("urgency", "")
        if q:
            qs = qs.filter(Q(request_code__icontains=q) | Q(organization__name__icontains=q)
                           | Q(patient_reference__icontains=q))
        if status:
            qs = qs.filter(status=status)
        if urgency:
            qs = qs.filter(urgency=urgency)
        channel = request.GET.get("channel", "")
        if channel in BloodRequest.Channel.values and request.user.role in ("ADMIN", "STAFF"):
            qs = qs.filter(channel=channel)
        else:
            channel = ""
        paginator = Paginator(qs, self.paginate_by)
        page_obj = paginator.get_page(request.GET.get("page"))
        ctx = {
            "requests_list": page_obj, "page_obj": page_obj, "q": q,
            "status_filter": status, "urgency_filter": urgency, "channel_filter": channel,
            "channels": BloodRequest.Channel.choices,
            "statuses": BloodRequest.Status.choices, "urgencies": BloodRequest.Urgency.choices,
            "is_staff_view": request.user.role in ("ADMIN", "STAFF"),
        }
        if is_htmx(request):
            return render(request, "requests/_table.html", ctx)
        return render(request, self.template_name, ctx)


class RequestBadgeView(View):
    """Sidebar pill: requests awaiting this user's side of the desk."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.role not in ("ADMIN", "STAFF", "REQUESTER"):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render(request, "components/nav_badge.html", {
            "count": BloodRequestService.awaiting_action_count(request.user),
        })


class RequestCreateView(View):
    template_name = "requests/form.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.role not in ("ADMIN", "STAFF", "REQUESTER"):
            raise PermissionDenied
        if request.user.role == "REQUESTER" and getattr(request.user, "requester_profile", None) is None:
            messages.error(request, "Your account is not linked to an organization. Contact staff.")
            return redirect("core:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def _is_staff(self, request):
        return request.user.role in ("ADMIN", "STAFF")

    def _wants_walk_in(self, request):
        channel = (request.POST.get("channel") or request.GET.get("channel") or "").upper()
        return self._is_staff(request) and channel == BloodRequest.Channel.WALK_IN

    def _organization(self, request, walk_in_org, walk_in):
        """Which organization owns the request being created.

        Requesters are pinned to their own; staff pick one, or the configured
        walk-in desk when logging a patient at the counter.
        """
        if not self._is_staff(request):
            return request.user.requester_profile.organization
        if walk_in:
            return walk_in_org
        org_id = request.POST.get("organization") or request.GET.get("organization")
        if not org_id:
            return None
        organization = get_object_or_404(Organization, pk=org_id, is_active=True)
        # The desk is reserved for walk-in records so an organization-channel
        # request can never be booked against it and then read as internal.
        return None if walk_in_org and organization.pk == walk_in_org.pk else organization

    def _context(self, request, form, organization, walk_in_org):
        is_staff = self._is_staff(request)
        organizations = Organization.objects.filter(is_active=True)
        if walk_in_org:
            organizations = organizations.exclude(pk=walk_in_org.pk)
        return {
            "form": form,
            "organization": organization,
            "organizations": organizations if is_staff else None,
            "walk_in_org": walk_in_org,
            "is_walk_in": bool(organization and walk_in_org and organization.pk == walk_in_org.pk),
            "is_staff_view": is_staff,
        }

    def get(self, request):
        walk_in_org = (BloodRequestService.walk_in_organization()
                       if self._is_staff(request) else None)
        walk_in = self._wants_walk_in(request)
        organization = self._organization(request, walk_in_org, walk_in)
        form = BloodRequestForm(
            staff=self._is_staff(request),
            # A "log a walk-in" entry point must open already on that card.
            initial={"channel": BloodRequest.Channel.WALK_IN} if walk_in else None,
        )
        return render_any(request, self.template_name,
                          self._context(request, form, organization, walk_in_org),
                          modal_title="New Blood Request")

    def post(self, request):
        walk_in = self._wants_walk_in(request)
        walk_in_org = (BloodRequestService.walk_in_organization()
                       if self._is_staff(request) else None)
        organization = self._organization(request, walk_in_org, walk_in)
        form = BloodRequestForm(request.POST, request.FILES, staff=self._is_staff(request))
        if organization is None:
            if walk_in and walk_in_org is None:
                form.add_error(None, "Walk-in intake is not configured: an administrator must point the "
                                     "walk_in_organization_id setting at the organization that owns counter "
                                     "requests (REQUIRES CONFIGURATION).")
            elif not walk_in and request.POST.get("organization"):
                form.add_error(None, "The walk-in desk organization only accepts walk-in records — "
                                     "choose the walk-in channel, or pick the requesting organization.")
            else:
                form.add_error(None, "Select an organization.")
        if form.is_valid() and organization:
            blood_request = form.save(commit=False)
            blood_request.organization = organization
            blood_request.created_by = request.user
            blood_request.status = (BloodRequest.Status.DRAFT if request.POST.get("save_draft")
                                    else BloodRequest.Status.SUBMITTED)
            blood_request.save()
            form.save_item(blood_request)
            via = f" (walk-in with {blood_request.walk_in_contact})" if blood_request.is_walk_in else ""
            audit.log(request, action="REQUEST_CREATED", module="requests", obj=blood_request,
                      description=f"Request {blood_request.request_code} created for {organization.name} "
                                  f"({blood_request.urgency}){via}")
            if blood_request.is_walk_in and blood_request.status == BloodRequest.Status.SUBMITTED:
                # Direct clinic request — validated by the counter, no approval queue.
                BloodRequestService.validate_walk_in(blood_request, actor=request.user, request=request)
                messages.success(request, f"Walk-in {blood_request.request_code} logged — "
                             f"compatible bags can be reserved now.")
            elif blood_request.status == BloodRequest.Status.SUBMITTED:
                if blood_request.is_emergency:
                    BloodRequestService._notify_staff_of_emergency(blood_request)
                messages.success(request, f"Request {blood_request.request_code} submitted.")
            else:
                messages.success(request, f"Draft {blood_request.request_code} saved.")
            return modal_success(request, fallback_redirect=reverse("requests:detail", args=[blood_request.pk]))
        return render_any(request, self.template_name,
                          self._context(request, form, organization, walk_in_org),
                          modal_title="New Blood Request")


class WalkInDeskView(StaffRequiredMixin, View):
    """The counter's own workspace: log a patient who walked in, and work the
    resulting records straight through to bag allocation — no approval queue."""

    template_name = "requests/walk_in_desk.html"
    paginate_by = 15

    def get(self, request):
        from django.core.paginator import Paginator

        qs = BloodRequestService.walk_in_queue()
        scope = request.GET.get("scope", "open")
        if scope == "open":
            qs = qs.filter(status__in=BloodRequest.OPEN_STATUSES)
        elif scope == "closed":
            qs = qs.exclude(status__in=BloodRequest.OPEN_STATUSES)
        page_obj = Paginator(qs, self.paginate_by).get_page(request.GET.get("page"))
        open_qs = BloodRequestService.walk_in_queue().filter(
            status__in=BloodRequest.OPEN_STATUSES)
        ctx = {
            "requests_list": page_obj, "page_obj": page_obj, "scope": scope,
            "desk": BloodRequestService.walk_in_organization(),
            "open_count": open_qs.count(),
            "owed_count": BloodRequestService.walk_in_action_count(),
            "closed_count": BloodRequestService.walk_in_queue().exclude(
                status__in=BloodRequest.OPEN_STATUSES).count(),
            "is_staff_view": True,
        }
        if is_htmx(request):
            return render(request, "requests/_table.html", ctx)
        return render(request, self.template_name, ctx)


class WalkInBadgeView(StaffRequiredMixin, View):
    """Sidebar pill: walk-ins still waiting on the counter to be filled."""

    def get(self, request):
        return render(request, "components/nav_badge.html", {
            "count": BloodRequestService.walk_in_action_count(),
        })


class RequestDetailView(View):
    template_name = "requests/detail.html"

    def get(self, request, pk):
        blood_request = _get_request_or_403(request.user, pk)
        is_staff = request.user.role in ("ADMIN", "STAFF")
        items = blood_request.items.select_related("blood_type", "component")
        ctx = {
            "blood_request": blood_request,
            "items": items,
            "allocations": blood_request.allocations.select_related("bag", "item", "allocated_by", "issued_by"),
            "notifications": blood_request.notifications.order_by("-created_at")[:10] if is_staff else [],
            "is_staff_view": is_staff,
        }
        if is_staff:
            ctx["compat_report"] = BloodRequestService.compatibility_report(blood_request)
            ctx["item_form"] = RequestItemAddForm()
            ctx["reject_form"] = RejectForm()
            # Attach each item's allocation form to the item so templates can
            # render it directly (queryset iteration, not dict lookup).
            for item in items:
                item.allocate_form = AllocateForm(item=item)
                item.compatible_count = item.allocate_form.fields["bag"].queryset.count()
            if blood_request.is_emergency:
                pool, shortage = BloodRequestService.emergency_donor_pool(blood_request, limit=25)
                ctx["donor_pool"], ctx["pool_shortage"] = pool, shortage
        ctx["modal_maxw"] = "max-w-5xl"
        return render_any(request, self.template_name, ctx,
                          modal_title=f"Request {blood_request.request_code}")


class RequestActionView(StaffRequiredMixin, View):
    """POST endpoint for workflow actions: submit/review/approve/reject/cancel/expire."""

    def post(self, request, pk):
        blood_request = get_object_or_404(BloodRequest, pk=pk)
        action = request.POST.get("action", "")
        try:
            if action == "submit":
                BloodRequestService.submit(blood_request, actor=request.user, request=request)
                messages.success(request, "Walk-in validated at the counter — compatible bags can "
                             "be reserved now." if blood_request.is_walk_in else "Request submitted.")
            elif action == "review":
                BloodRequestService.start_review(blood_request, actor=request.user, request=request)
                messages.success(request, "Review started.")
            elif action == "approve":
                BloodRequestService.approve(blood_request, actor=request.user, request=request)
                messages.success(request, "Request approved. You can now allocate compatible bags.")
            elif action == "reject":
                reason = request.POST.get("reason", "")
                BloodRequestService.reject(blood_request, reason=reason, actor=request.user, request=request)
                messages.success(request, "Request rejected.")
            elif action == "cancel":
                reason = request.POST.get("reason", "")
                BloodRequestService.cancel(blood_request, reason=reason, actor=request.user, request=request)
                messages.success(request, "Request cancelled.")
            elif action == "add_item":
                form = RequestItemAddForm(request.POST)
                if form.is_valid():
                    item = form.save(commit=False)
                    item.request = blood_request
                    item.save()
                    audit.log(request, action="REQUEST_ITEM_ADDED", module="requests", obj=item,
                              description=f"Item {item.quantity} × {item.blood_type} {item.component} "
                                          f"added to {blood_request.request_code}")
                    messages.success(request, "Item added.")
                else:
                    messages.error(request, "Invalid item data.")
            elif action == "allocate":
                item = get_object_or_404(RequestItem, pk=request.POST.get("item"), request=blood_request)
                form = AllocateForm(request.POST, item=item)
                if form.is_valid():
                    BloodRequestService.allocate_bag(item, form.cleaned_data["bag"],
                                                     actor=request.user, request=request)
                    messages.success(request, f"Bag {form.cleaned_data['bag'].bag_code} reserved.")
                else:
                    on_hand = form.fields["bag"].queryset.count()
                    label = f"{item.blood_type.code} {item.component.name}"
                    if not item.reservable_units:
                        messages.error(request, f"All {item.quantity} unit(s) of {label} are already "
                                                f"reserved or issued — issue or cancel first.")
                    elif on_hand:
                        messages.error(request, f"Pick one of the {on_hand} compatible bag(s) "
                                                f"on hand for {label}.")
                    else:
                        messages.error(request, f"No compatible AVAILABLE bag in stock for "
                                                f"{label} — {item.outstanding} unit(s) still short. "
                                                f"See the Compatibility Report below.")
            elif action == "cancel_allocation":
                allocation = get_object_or_404(Allocation, pk=request.POST.get("allocation"),
                                               request=blood_request)
                BloodRequestService.cancel_allocation(allocation, actor=request.user, request=request,
                                                      reason=request.POST.get("reason", ""))
                messages.success(request, "Allocation cancelled; bag returned to available stock.")
            elif action == "issue":
                allocation = get_object_or_404(Allocation, pk=request.POST.get("allocation"),
                                               request=blood_request)
                if request.POST.get("confirm") != "ISSUE":
                    messages.error(request, "Issue was not confirmed.")
                else:
                    BloodRequestService.issue_allocation(allocation, actor=request.user, request=request)
                    messages.success(request, f"Bag {allocation.bag.bag_code} issued against "
                                              f"{blood_request.request_code}.")
            elif action == "emergency_notify":
                sent, failed = BloodRequestService.trigger_emergency_notification(
                    blood_request, actor=request.user, request=request,
                )
                messages.success(request, f"Emergency alerts: {len(sent)} sent"
                                          + (f", {len(failed)} failed" if failed else "."))
            else:
                messages.error(request, f"Unknown action '{action}'.")
        except RequestError as exc:
            messages.error(request, str(exc))
        return modal_success(request, fallback_redirect=reverse("requests:detail", args=[pk]))


class RequesterFulfillmentView(View):
    """Requester-side feedback: mark transfused or return unused blood."""

    def post(self, request, pk, allocation_pk):
        blood_request = _get_request_or_403(request.user, pk)
        allocation = get_object_or_404(Allocation, pk=allocation_pk, request=blood_request)
        action = request.POST.get("action")
        try:
            if action == "transfused":
                BloodRequestService.mark_transfused(allocation, actor=request.user, request=request)
                messages.success(request, "Transfusion recorded. Thank you for closing the loop.")
            elif action == "return":
                BloodRequestService.return_allocation(
                    allocation, actor=request.user, request=request,
                    reason=request.POST.get("reason", "Returned by requester"))
                messages.success(request, "Return recorded.")
            else:
                messages.error(request, "Unknown action.")
        except (RequestError, Exception) as exc:  # noqa: BLE001
            messages.error(request, str(exc))
        return modal_success(request, fallback_redirect=reverse("requests:detail", args=[pk]))


# --- Organization management (admin) -------------------------------------------
class OrganizationListView(StaffRequiredMixin, ListView):
    template_name = "requests/organization_list.html"
    context_object_name = "organizations"
    paginate_by = 20

    def get_queryset(self):
        qs = Organization.objects.all()
        q = self.request.GET.get("q", "").strip()
        return qs.filter(name__icontains=q) if q else qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        return ctx


class OrganizationFormView(StaffRequiredMixin, View):
    template_name = "requests/organization_form.html"

    def get(self, request, pk=None):
        organization = get_object_or_404(Organization, pk=pk) if pk else None
        return render_any(request, self.template_name,
                          {"form": OrganizationForm(instance=organization), "organization": organization},
                          modal_title=("Edit " + organization.name) if organization else "New Organization")

    def post(self, request, pk=None):
        organization = get_object_or_404(Organization, pk=pk) if pk else None
        form = OrganizationForm(request.POST, instance=organization)
        if form.is_valid():
            org = form.save()
            audit.log(request, action="ORGANIZATION_CREATED" if organization is None else "ORGANIZATION_UPDATED",
                      module="requests", obj=org, description=f"Organization '{org.name}' saved")
            messages.success(request, "Organization saved.")
            return modal_success(request, fallback_redirect=reverse("requests:organization_list"))
        return render_any(request, self.template_name,
                          {"form": form, "organization": organization},
                          modal_title=(f"Edit {organization.name}" if organization else "New Organization"))
