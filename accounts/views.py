import logging

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import ListView

from accounts.forms import (
    AdminUserCreateForm,
    AdminUserForm,
    BloodBankAuthenticationForm,
    ProfileForm,
    StyledPasswordChangeForm,
)
from accounts.models import User
from audit import services as audit
from core.mixins import AdminRequiredMixin, is_htmx
from core.modals import MODAL_LAYOUT, modal_success, render_any

logger = logging.getLogger("bloodbank.auth")


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = BloodBankAuthenticationForm
    redirect_authenticated_user = False  # handled below so ?next= is honoured

    def dispatch(self, request, *args, **kwargs):
        # `/` is now the public landing page, so an authenticated user hitting
        # the login screen goes to the dashboard — unless a ?next= brought
        # them here (is_safe_url keeps it on-site, same as FormMixin).
        if request.user.is_authenticated:
            nxt = request.GET.get("next") or request.POST.get("next")
            if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts=None):
                return redirect(nxt)
            return redirect("core:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        logger.info("LOGIN_SUCCESS user=%s ip=%s", self.request.user.username or form.get_user().username,
                    self.request.META.get("REMOTE_ADDR"))
        audit.log(self.request, user=form.get_user(), action="USER_LOGIN", module="accounts",
                  description="Successful login")
        return super().form_valid(form)

    def form_invalid(self, form):
        logger.warning("LOGIN_FAILED username=%s ip=%s",
                       form.data.get("username"), self.request.META.get("REMOTE_ADDR"))
        return super().form_invalid(form)


class LogoutView(auth_views.LogoutView):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            audit.log(request, action="USER_LOGOUT", module="accounts", description="Logout")
        return super().dispatch(request, *args, **kwargs)


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    form_class = StyledPasswordChangeForm
    success_url = reverse_lazy("accounts:profile")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if is_htmx(self.request):
            ctx["layout"] = MODAL_LAYOUT
            ctx.setdefault("modal_title", "Change Password")
        return ctx

    def form_valid(self, form):
        audit.log(self.request, action="USER_PASSWORD_CHANGED", module="accounts",
                  obj=self.request.user, description="Password changed")
        messages.success(self.request, "Password updated successfully.")
        # super() saves the new password and refreshes the session auth hash;
        # we only replace the redirect with the modal-aware response.
        result = super().form_valid(form)
        if is_htmx(self.request):
            return modal_success(self.request,
                                 fallback_redirect=reverse("accounts:profile"))
        return result


# Password reset uses Django's token flow; email backend defaults to console
# in development (clearly not a live email service unless EMAIL_* configured).
class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "accounts/password_reset_email.txt"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:login")


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


def profile(request):
    """Own profile; donors may only update permitted personal information."""
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            before = {"email": request.user.email, "phone": request.user.phone,
                      "first_name": request.user.first_name, "last_name": request.user.last_name}
            form.save()
            audit.log(request, action="USER_PROFILE_UPDATED", module="accounts",
                      obj=request.user, before=before,
                      after={"email": form.instance.email, "phone": form.instance.phone,
                             "first_name": form.instance.first_name, "last_name": form.instance.last_name},
                      description="Profile updated")
            messages.success(request, "Profile updated.")
            return modal_success(request, fallback_redirect=reverse("accounts:profile"))
    else:
        form = ProfileForm(instance=request.user)
    donor = getattr(request.user, "donor_profile", None)
    requester = getattr(request.user, "requester_profile", None)
    return render_any(request, "accounts/profile.html",
                      {"form": form, "donor": donor, "requester": requester,
                       "modal_maxw": "max-w-5xl"},
                      modal_title="My Profile")


# --- Admin user management ---------------------------------------------------------
class UserListView(AdminRequiredMixin, ListView):
    template_name = "accounts/user_list.html"
    context_object_name = "users"
    paginate_by = 20

    def get_queryset(self):
        qs = User.objects.all().order_by("username")
        q = self.request.GET.get("q", "").strip()
        role = self.request.GET.get("role", "")
        status = self.request.GET.get("status", "")
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q)
                           | Q(last_name__icontains=q) | Q(email__icontains=q))
        if role:
            qs = qs.filter(role=role)
        if status == "active":
            qs = qs.filter(is_active=True, is_locked=False)
        elif status == "locked":
            qs = qs.filter(Q(is_locked=True) | Q(locked_until__isnull=False))
        elif status == "inactive":
            qs = qs.filter(is_active=False)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["role_filter"] = self.request.GET.get("role", "")
        ctx["status_filter"] = self.request.GET.get("status", "")
        ctx["roles"] = User.Role.choices
        return ctx


class UserCreateView(AdminRequiredMixin, View):
    template_name = "accounts/user_form.html"

    def get(self, request):
        return render_any(request, self.template_name,
                          {"form": AdminUserCreateForm(), "creating": True},
                          modal_title="Create User")

    def post(self, request):
        form = AdminUserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            audit.log(request, action="USER_CREATED", module="accounts", obj=user,
                      description=f"Created user {user.username} with role {user.role}")
            messages.success(request, f"User {user.username} created.")
            return modal_success(request, fallback_redirect=reverse("accounts:user_list"))
        return render_any(request, self.template_name, {"form": form, "creating": True},
                          modal_title="Create User")


class UserUpdateView(AdminRequiredMixin, View):
    template_name = "accounts/user_form.html"

    def get(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        return render_any(request, self.template_name,
                          {"form": AdminUserForm(instance=user), "edit_user": user, "creating": False},
                          modal_title=f"Edit User — {user.username}")

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        before = {"role": user.role, "is_active": user.is_active, "is_locked": user.is_locked,
                  "email": user.email}
        form = AdminUserForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            changed = {k: (before[k], getattr(user, k)) for k in before if before[k] != getattr(user, k)}
            action = "USER_ROLE_CHANGED" if "role" in changed else "USER_UPDATED"
            audit.log(request, action=action, module="accounts", obj=user, before=before,
                      after={k: getattr(user, k) for k in before},
                      description=f"Updated user {user.username}" + (f" (changed: {', '.join(changed)})" if changed else ""))
            messages.success(request, f"User {user.username} updated.")
            return modal_success(request, fallback_redirect=reverse("accounts:user_list"))
        return render_any(request, self.template_name, {"form": form, "edit_user": user, "creating": False},
                          modal_title=f"Edit User — {user.username}")


class UserLockToggleView(AdminRequiredMixin, View):
    """Lock/unlock is a guarded access change: the trigger opens a confirmation
    modal (GET) which posts back to this same URL."""

    confirm_template = "accounts/user_lock_confirm.html"

    def get(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        title = "Unlock User" if user.is_locked else "Lock User"
        return render_any(request, self.confirm_template, {"target_user": user},
                          modal_title=title)

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        if user == request.user:
            messages.error(request, "You cannot lock your own account.")
            return modal_success(request, fallback_redirect=reverse("accounts:user_list"))
        if user.role == "ADMIN" and not user.is_locked:
            others = User.objects.filter(role="ADMIN", is_active=True, is_locked=False).exclude(pk=user.pk).exists()
            if not others:
                messages.error(request, "Cannot lock the last active administrator.")
                return modal_success(request, fallback_redirect=reverse("accounts:user_list"))
        user.is_locked = not user.is_locked
        user.locked_until = None
        user.save(update_fields=["is_locked", "locked_until"])
        audit.log(request, action="USER_LOCKED" if user.is_locked else "USER_UNLOCKED",
                  module="accounts", obj=user,
                  description=f"{'Locked' if user.is_locked else 'Unlocked'} user {user.username}")
        messages.success(request, f"User {user.username} {'locked' if user.is_locked else 'unlocked'}.")
        return modal_success(request, fallback_redirect=reverse("accounts:user_list"))
