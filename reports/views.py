"""Reporting center views: HTML tables + CSV export, role-gated."""
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.views import View

from core.mixins import StaffRequiredMixin, is_htmx
from inventory.models import BloodType
from reports.engine import REPORTS, write_csv
from donations.models import Donation
from requests.models import BloodRequest


def _report_or_403(user, key):
    report = REPORTS.get(key)
    if report is None:
        raise PermissionDenied
    if user.role not in report["roles"]:
        raise PermissionDenied(f"Role {user.role} may not run this report.")
    return report


class ReportCenterView(StaffRequiredMixin, View):
    def get(self, request):
        groups = {}
        for key, report in REPORTS.items():
            if request.user.role in report["roles"]:
                groups.setdefault(report["group"], []).append((key, report))
        return render(request, "reports/center.html", {"groups": groups})


class ReportRunView(StaffRequiredMixin, View):
    template_name = "reports/report.html"

    def get(self, request, key):
        report = _report_or_403(request.user, key)
        params = request.GET
        qs = report["queryset"](params)
        rows = report["rows"](qs)
        paginator = Paginator(rows, 50)
        page_obj = paginator.get_page(params.get("page"))
        ctx = {
            "report_key": key, "report": report, "rows": page_obj.object_list,
            "page_obj": page_obj, "columns": report["columns"],
            "filters": {f: params.get(f, "") for f in report["filters"]},
            "blood_types": BloodType.objects.filter(is_active=True),
            "donation_statuses": Donation.Status.choices,
            "request_statuses": BloodRequest.Status.choices,
            "bag_statuses": None,
        }
        from inventory.models import BloodBag
        from donors.models import Donor
        ctx["bag_statuses"] = BloodBag.Status.choices
        ctx["donor_statuses"] = Donor.Status.choices
        if is_htmx(request):
            return render(request, "reports/_table.html", ctx)
        return render(request, self.template_name, ctx)


class ReportExportView(StaffRequiredMixin, View):
    def get(self, request, key):
        report = _report_or_403(request.user, key)
        qs = report["queryset"](request.GET)
        rows = report["rows"](qs)
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{key}_report.csv"'
        return write_csv(response, key, rows)
