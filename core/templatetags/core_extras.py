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

    The look of the link lives in the stylesheet (``.bb-nav-item`` in
    static/css/input.css) so the sidebar can be re-skinned without touching
    this tag; only the structure is produced here.
    """
    try:
        url = reverse(url_name)
    except NoReverseMatch:
        return ""
    current = request.path
    active = current == url or (url != "/" and current.startswith(url))
    cls = "bb-nav-item" + (" is-active" if active else "")
    icon = format_html(
        '<span class="bb-nav-icon"><svg class="w-[17px] h-[17px]" fill="none" stroke="currentColor" '
        'stroke-width="1.75" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" '
        'stroke-linejoin="round" d="{}"/></svg></span>',
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


# --- Unified Clinical Icon Library ------------------------------------------------
# Consistent 24x24 outline icons (1.75 stroke, rounded caps/joins) used by
# stat cards, alerts, and shared components so icon weight and geometry stay
# uniform across every screen.
_ICONS = {
    "users": "M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z",
    "droplet": "M12 3.25c0 0-6.25 7.25-6.25 11.5a6.25 6.25 0 1 0 12.5 0c0-4.25-6.25-11.5-6.25-11.5Z",
    "cube": "m21 7.5-9-5.25L3 7.5m18 0-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25m0-9v9",
    "clipboard": "M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2",
    "clipboard-check": "M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2m-6 9 2 2 4-4",
    "alert": "M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.008v.008H12v-.008Z",
    "clock": "M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
    "beaker": "M9.75 3.104v5.714a2.25 2.25 0 0 1-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 0 1 4.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0 1 12 15a9.065 9.065 0 0 0-6.23-.693L5 14.5m14.8.8 1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0 1 12 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5",
    "check-circle": "M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
    "x-circle": "m9.75 9.75 4.5 4.5m0-4.5-4.5 4.5M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
    "document-text": "M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5A3.375 3.375 0 0 0 10.125 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Zm-11.25.75h7.5m-7.5 3h4.5",
    "calendar": "M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5",
    "award": "M16.5 18.75h-9m9 0a3 3 0 0 1 3 3h-15a3 3 0 0 1 3-3m9 0v-3.375c0-.621-.503-1.125-1.125-1.125h-.871M7.5 18.75v-3.375c0-.621.504-1.125 1.125-1.125h.872m5.007 0H9.497m5.007 0a7.454 7.454 0 0 1-.982-3.172M9.497 14.25a7.454 7.454 0 0 0 .981-3.172M5.25 4.236c-.982.143-1.954.317-2.916.52A6.003 6.003 0 0 0 7.73 9.728M5.25 4.236V4.5c0 2.108.966 3.99 2.48 5.228M5.25 4.236V2.721C7.456 2.41 9.71 2.25 12 2.25c2.291 0 4.545.16 6.75.47v1.516M7.73 9.728a6.726 6.726 0 0 0 2.748 1.35m8.272-6.842V4.5c0 2.108-.966 3.99-2.48 5.228m2.48-5.492a46.32 46.32 0 0 1 2.916.52 6.003 6.003 0 0 1-5.395 4.972m0 0a6.726 6.726 0 0 1-2.749 1.35m0 0a6.772 6.772 0 0 1-3.044 0",
    "sparkles": "M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 0 0-2.456 2.456Z",
    "shield-check": "M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285Z",
    "info": "m11.25 11.25.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836a.75.75 0 0 0 1.063.853l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-3.75h.008v.008H12V8.25Z",
}


@register.simple_tag
def icon(name, css_class="w-4 h-4"):
    """Render a consistent 24x24 outline icon from the clinical icon set."""
    path = _ICONS.get(name)
    if not path:
        return ""
    return format_html(
        '<svg class="{} shrink-0" fill="none" stroke="currentColor" stroke-width="1.75" '
        'viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" '
        'stroke-linejoin="round" d="{}"/></svg>',
        css_class,
        path,
    )
