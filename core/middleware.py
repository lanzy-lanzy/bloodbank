"""Global middleware: enforce authentication for every page except a public allowlist."""
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import resolve_url

PUBLIC_PATHS = (
    resolve_url(settings.LOGIN_URL),   # LOGIN_URL may be a dotted name — compare paths, not names
    "/accounts/password-reset/",
    "/accounts/reset/",                # password-reset confirm links must work for anonymous users
    "/notifications/respond/",         # token-based donor response links
    "/static/",
    "/media/",
)

# Exact-match public paths: "/" must NOT go into PUBLIC_PATHS, because
# startswith("/") would match every URL and disable auth entirely.
PUBLIC_PATHS_EXACT = ("/",)


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        is_public = path in PUBLIC_PATHS_EXACT or any(path.startswith(p) for p in PUBLIC_PATHS)
        if not is_public and not request.user.is_authenticated:
            # HTMX requests get a 401 so htmx can trigger a full-page redirect.
            if request.headers.get("HX-Request"):
                from django.http import HttpResponse

                response = HttpResponse(status=401)
                response["HX-Redirect"] = settings.LOGIN_URL
                return response
            return redirect_to_login(path, settings.LOGIN_URL)
        return self.get_response(request)
