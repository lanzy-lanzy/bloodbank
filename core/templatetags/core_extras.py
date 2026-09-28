"""Shared template tags and filters for the UI layer."""
import os

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.urls import NoReverseMatch, Resolver404, resolve, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag
def static_v(path):
    """Static URL with a build-version query string, e.g. /static/css/tailwind.css?v=172...

    Locally built assets (Tailwind output) keep the same filename across
    rebuilds, so browsers happily serve the stale cached copy until a hard
    refresh. Appending the file's mtime+size makes the URL change the moment
    `npm run watch:css` rebuilds — a normal navigation picks up new styles.
    """
    url = static(path)
    abs_path = finders.find(path.split("?")[0])
    if abs_path:
        try:
            st = os.stat(abs_path)
            url = f"{url}?v={int(st.st_mtime)}-{st.st_size}"
        except OSError:
            pass
    return url

# --- Status presentation ------------------------------------------------------
# Colour mapping is presentational only; every badge also renders the status
# text so information is never conveyed by colour alone (accessibility).
_STATUS_COLORS = {
    # inventory / blood bag
    "AVAILABLE": "green", "TRANSFUSED": "green", "COMPLETED": "green", "CLEARED": "green",
    "FULFILLED": "green", "APPROVED": "green", "NON_REACTIVE": "green", "ACCEPTABLE": "green",
    "SENT": "green", "DELIVERED": "green", "SUCCESS": "green", "CONFIRMED": "green",
    "ACTIVE": "green", "ELIGIBLE": "green", "CHECKED_IN": "sky", "COLLECTED": "sky",
    "RESERVED": "sky", "UNDER_REVIEW": "sky", "SCREENING": "sky", "TESTING": "violet",
    "IN_PROGRESS": "violet", "PROCESSING": "violet", "PARTIALLY_FULFILLED": "amber",
    "QUARANTINED": "amber", "PENDING": "amber", "REQUESTED": "amber", "SUBMITTED": "amber",
    "REQUIRES_REVIEW": "amber", "REQUIRES_STAFF_REVIEW": "amber", "REQUIRES_RETEST": "amber",
    "DEFERRED": "amber", "TEMP_DEFERRED": "amber", "MAYBE": "amber", "URGENT": "amber",
    "EMERGENCY": "red", "CRITICAL": "red", "EXPIRED": "red", "REJECTED": "red",
    "DISCARDED": "red", "FAILED": "red", "REACTIVE": "red", "INVALID": "red",
    "BLACKLISTED": "red", "PERM_DEFERRED": "red", "NOT_ELIGIBLE": "red", "LOCKED": "red",
    "CANCELLED": "slate", "INACTIVE": "slate", "NO_SHOW": "slate", "RETURNED": "slate",
    "ISSUED": "sky", "DRAFT": "slate", "UNAVAILABLE": "slate", "RELEASED": "green",
    "REDEEMED": "violet", "CONTACT_ME": "sky",
}


@register.filter
def status_color(status):
    return _STATUS_COLORS.get(str(status).upper(), "slate")


@register.filter
def display_status(status):
    return str(status).replace("_", " ").title()


# --- Role checks ---------------------------------------------------------------
@register.filter
def has_role(user, role):
    return user.is_authenticated and getattr(user, "role", None) == role


@register.filter
def has_any_role(user, roles_csv):
    if not user.is_authenticated:
        return False
    roles = [r.strip() for r in roles_csv.split(",")]
    return getattr(user, "role", None) in roles


# --- Navigation ------------------------------------------------------------------
@register.simple_tag
def nav_item(url_name, label, request, icon_path, badge_url=""):
    """Sidebar link with active-state highlighting.

    ``badge_url`` is optional: a fragment endpoint that renders
    ``components/nav_badge.html`` and is polled by htmx, so the count stays
    live without a full navigation (the sidebar is not part of #page-content).
    """
    try:
        url = reverse(url_name)
    except NoReverseMatch:
        return ""
    current = request.path
    active = current == url or (url != "/" and current.startswith(url))
    cls = ("flex items-center gap-3 px-3 py-2 rounded-xl transition-colors "
           + ("bg-brand-600/20 text-white font-medium ring-1 ring-inset ring-brand-500/30" if active
              else "text-ink-400 hover:text-white hover:bg-ink-800"))
    icon = format_html(
        '<svg class="w-5 h-5 shrink-0" fill="none" stroke="currentColor" stroke-width="1.8" '
        'viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="{}"/></svg>',
        icon_path,
    )
    badge = format_html(
        '<span hx-get="{}" hx-trigger="load, every 60s" hx-swap="innerHTML"></span>',
        badge_url,
    ) if badge_url else ""
    return format_html('<a href="{}" class="{}">{}<span>{}</span>{}</a>',
                       url, cls, mark_safe(icon), label, mark_safe(badge))


# --- Topbar section title --------------------------------------------------------
# The topbar shows the section the visitor is currently in, mirroring whichever
# sidebar item is highlighted. Mapping by URL namespace (with a handful of
# exact-name overrides) means it updates automatically on navigation without
# touching individual templates; detail/create/edit sub-pages fall back to
# their parent section label so the title never goes blank.
_SECTION_BY_NAMESPACE = {
    "core": "Dashboard",
    "donors": "Donors",
    "appointments": "Appointments",
    "donations": "Donations",
    "inventory": "Inventory",
    "requests": "Blood Requests",
    "rewards": "Rewards",
    "notifications": "Notifications",
    "reports": "Reports",
    "settings_app": "System Settings",
    "audit": "Audit Logs",
    "accounts": "Account",
}

_SECTION_BY_NAME = {
    "donors:my_profile": "My Profile",
    "donations:my_history": "Donation History",
    "appointments:my_appointments": "My Appointments",
    "rewards:my_rewards": "Points & Rewards",
    "rewards:admin_overview": "Rewards Program",
    "requests:organization_list": "Organizations",
    "inventory:bag_list": "Blood Bags",
    "notifications:delivery_list": "Delivery Overview",
    "notifications:template_list": "Message Templates",
    "settings_app:blood_bank": "Blood Bank Configuration",
    "accounts:profile": "My Profile",
    "accounts:user_list": "Users",
    "accounts:password_change": "Change Password",
}


@register.simple_tag
def section_title(request, default="Dashboard"):
    """Human label for the current page's nav section, derived from its URL."""
    try:
        match = resolve(request.path)
    except (Resolver404, ValueError):
        return default
    namespace = match.namespace
    url_name = f"{namespace}:{match.url_name}" if namespace else match.url_name
    if url_name in _SECTION_BY_NAME:
        return _SECTION_BY_NAME[url_name]
    if namespace in _SECTION_BY_NAMESPACE:
        return _SECTION_BY_NAMESPACE[namespace]
    return default


# --- Pagination -------------------------------------------------------------------
@register.filter
def slice_pages(page_range, current):
    """Compact page list: 1 … 4 5 6 … 20 style, with '…' separators."""
    pages = list(page_range)
    if len(pages) <= 7:
        return pages
    out = []
    for p in pages:
        if p == 1 or p == pages[-1] or abs(p - current) <= 1:
            out.append(p)
        elif out and out[-1] != "…":
            out.append("…")
    return out
