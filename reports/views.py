"""Reporting centre views: HTML tables, print preview, PDF and CSV export.

Every output path goes through the same ``_report_or_403`` role gate, so a
report a role may not *run* is not printable or exportable either — the
document/PDF actions are not a way around the registry's ``roles`` list.
"""
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from core.documents import build_document
from core.mixins import PrintableDocumentMixin, StaffRequiredMixin, is_htmx
from inventory.models import BloodBag, BloodType
from reports.engine import REPORTS, active_filter_labels, is_landscape, write_csv
from donations.models import Donation
from requests.models import BloodRequest


def _report_or_403(user, key):
    report = REPORTS.get(key)
    if report is None:
        raise PermissionDenied
    if user.role not in report["roles"]:
        raise PermissionDenied(f"Role {user.role} may not run this report.")
    return report


def _actor_label(user) -> str:
    """A printed document must name a person, not a session or a primary key."""
    name = (getattr(user, "get_full_name", lambda: "")() or "").strip()
    return f"{name} ({user.get_username()})" if name else user.get_username()


def _url_with_query(request, name, key):
    """Reverse ``name`` for ``key``, carrying the active filters across.

    The page cursor is deliberately dropped: a printed document is the whole
    result set, so inheriting ``?page=3`` from a paginated screen would print a
    misleading slice.
    """
    url = reverse(name, kwargs={"key": key})
    kept = [p for p in request.GET.urlencode().split("&")
            if p and not p.startswith("page=")]
    return f"{url}?{'&'.join(kept)}" if kept else url



class ReportCenterView(StaffRequiredMixin, View):
    def get(self, request):
        groups = {}
        for key, report in REPORTS.items():
            if request.user.role in report["roles"]:
                groups.setdefault(report["group"], []).append((key, report))
        return render(request, "reports/center.html", {
            "groups": groups,
            "report_count": sum(len(items) for items in groups.values()),
        })


class ReportRunView(StaffRequiredMixin, View):
    template_name = "reports/report.html"

    def get(self, request, key):
        from donors.models import Donor
        report = _report_or_403(request.user, key)
        params = request.GET
        rows = report["rows"](report["queryset"](params))
        paginator = Paginator(rows, 50)
        page_obj = paginator.get_page(params.get("page"))
        ctx = {
            "report_key": key, "report": report, "rows": page_obj.object_list,
            "page_obj": page_obj, "columns": report["columns"],
            "filters": {f: params.get(f, "") for f in report["filters"]},
            "total_rows": paginator.count,
            "blood_types": BloodType.objects.filter(is_active=True),
            "donation_statuses": Donation.Status.choices,
            "request_statuses": BloodRequest.Status.choices,
            "bag_statuses": BloodBag.Status.choices,
            "donor_statuses": Donor.Status.choices,
            # Print / PDF / CSV links, carrying the active filters but not the
            # page cursor: a printed document is the whole result set.
            "document_url": _url_with_query(request, "reports:document", key),
            "pdf_url": _url_with_query(request, "reports:pdf", key),
            "csv_url": _url_with_query(request, "reports:export", key),
        }
        if is_htmx(request):
            return render(request, "reports/_table.html", ctx)
        return render(request, self.template_name, ctx)


class ReportExportView(StaffRequiredMixin, View):
    def get(self, request, key):
        report = _report_or_403(request.user, key)
        rows = report["rows"](report["queryset"](request.GET))
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{key}_report.csv"'
        return write_csv(response, key, rows)


class _ReportDocumentMixin(PrintableDocumentMixin):
    """Shared spec builder for the print-preview and PDF actions of a report.

    Both actions are registered on views that keep ``StaffRequiredMixin`` *and*
    call ``_report_or_403`` in ``get()`` before any data access, so the ordering
    "check the role, then read the rows" is visible at the entry point rather
    than hidden inside the spec builder. The builder re-checks too: it is the
    only thing standing between an unknown key and the data layer.
    """

    document_url = "reports:document"
    pdf_url = "reports:pdf"
    csv_url = "reports:export"
    back_url = "reports:run"

    def build_document_spec(self, request, *args, **kwargs):
        key = kwargs.get("key") or args[0]
        report = _report_or_403(request.user, key)
        rows = report["rows"](report["queryset"](request.GET))
        generated = timezone.localtime()
        prepared_by = _actor_label(request.user)
        scope = "; ".join(f"{label}: {value}"
                          for label, value in active_filter_labels(key, request.GET))
        notes = [
            f"Scope applied - {scope}.",
            "Figures are extracted live from the system at the moment of generation.",
        ]
        if report["roles"] == ["ADMIN"]:
            notes.append("Restricted document: administrator access only.")
        return build_document(
            title=report["title"],
            doc_type="REPORT",
            subtitle=report.get("description", ""),
            columns=report["columns"],
            rows=rows,
            meta=[
                ("Generated on", generated.strftime("%d %b %Y, %H:%M")),
                ("Generated by", prepared_by),
                ("Records", f"{len(rows):,}"),
                ("Category", report["group"]),
            ],
            landscape=is_landscape(key),
            notes=notes,
            prepared_by=prepared_by,
            pdf_filename=f"{key}_{generated:%Y%m%d_%H%M}.pdf",
        )


class ReportDocumentView(StaffRequiredMixin, _ReportDocumentMixin, View):
    """Print preview for a report: the formal page, plus a print/PDF toolbar."""

    def get(self, request, key):
        _report_or_403(request.user, key)  # role gate, before any data access
        return self.document(request, key=key)


class ReportPdfView(StaffRequiredMixin, _ReportDocumentMixin, View):
    """The same report as a downloadable PDF file."""

    def get(self, request, key):
        _report_or_403(request.user, key)  # role gate, before any data access
        return self.pdf(request, key=key)
