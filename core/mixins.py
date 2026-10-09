"""Role-based access-control mixins used by all module views.

Roles are enforced here AND by Django permissions; object-level checks live in
the individual views/services (never rely on the UI hiding a link).
"""
from dataclasses import replace

from django.contrib import messages
from django.contrib.auth.mixins import AccessMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from core.documents import DocumentRenderError, pdf_response, render_document


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


class PrintableDocumentMixin:
    """Adds a print-preview action and a PDF-download action to a view.

    Both build the *same* ``DocumentSpec`` (see :mod:`core.documents`), which is
    what guarantees a printed page and a downloaded file show identical rows,
    filters and timestamps. A concrete view implements
    :meth:`build_document_spec` and names the routes to link back to:

    ``document_url``  this view's own print-preview route
    ``pdf_url``       this view's own PDF route
    ``csv_url``       optional CSV route offered alongside (may be "")
    ``back_url``      where the preview's Close button returns

    Role gating is deliberately *not* handled here: the concrete view keeps the
    same ``StaffRequiredMixin`` / ``AdminRequiredMixin`` it already had, so a
    report a role may not run stays un-printable and un-exportable too.
    """

    document_url: str = ""
    pdf_url: str = ""
    csv_url: str = ""
    back_url: str = ""

    def build_document_spec(self, request, *args, **kwargs):
        """Return the :class:`~core.documents.DocumentSpec` for this request."""
        raise NotImplementedError

    def _route_with_query(self, name, kwargs):
        """Reverse ``name``, carrying the active filters over as a query string."""
        if not name:
            return None
        url = reverse(name, kwargs=kwargs)
        query = self.request.GET.urlencode()
        return f"{url}?{query}" if query else url

    def _toolbar(self, kwargs) -> dict:
        # The Print button needs no URL: the preview page *is* the document, so
        # its button calls window.print() on the page it is already on. The other
        # actions are real routes, carrying the active filters across.
        return {
            "pdf": self._route_with_query(self.pdf_url, kwargs),
            "csv": self._route_with_query(self.csv_url, kwargs),
            "back": reverse(self.back_url, kwargs=kwargs) if self.back_url else None,
        }

    def document(self, request, *args, **kwargs):
        """Print preview: the formal page in a browser, with an action toolbar."""
        spec = replace(self.build_document_spec(request, *args, **kwargs),
                       toolbar=self._toolbar(kwargs))
        return HttpResponse(render_document(request, spec, chrome=True))

    def pdf(self, request, *args, **kwargs):
        """Stream the same document as a real, downloadable PDF file."""
        spec = self.build_document_spec(request, *args, **kwargs)
        try:
            return pdf_response(request, spec)
        except DocumentRenderError as exc:
            # Never hand back a file that is not a valid PDF: explain, and send
            # the operator to the print preview, which always works.
            messages.error(request, f"PDF export is unavailable right now ({exc}). "
                                   "Use the print preview and choose 'Save as PDF'.")
            return redirect(reverse(self.document_url, kwargs=kwargs))

