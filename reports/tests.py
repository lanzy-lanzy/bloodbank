"""Reports: role gating, rendering, CSV export, print preview and PDF."""
from django.test import TestCase
from django.urls import reverse

from core.documents import build_columns, pdf_safe_text
from core.testing import make_blood_type, make_bag, make_component, make_donor, make_user
from reports.engine import REPORTS, active_filter_labels, is_landscape


class ReportAccessTests(TestCase):
    def setUp(self):
        self.staff = make_user("rp-staff", role="STAFF")
        self.admin = make_user("rp-admin", role="ADMIN")
        self.donor_user = make_user("rp-donor", role="DONOR")
        make_donor(user=self.donor_user)

    def test_staff_can_run_shared_report(self):
        self.client.login(username="rp-staff", password="Test12345!")
        resp = self.client.get(reverse("reports:run", kwargs={"key": "inventory"}))
        self.assertEqual(resp.status_code, 200)

    def test_audit_report_is_admin_only(self):
        self.client.login(username="rp-staff", password="Test12345!")
        resp = self.client.get(reverse("reports:run", kwargs={"key": "audit"}))
        self.assertEqual(resp.status_code, 403)
        self.client.login(username="rp-admin", password="Test12345!")
        resp = self.client.get(reverse("reports:run", kwargs={"key": "audit"}))
        self.assertEqual(resp.status_code, 200)

    def test_donor_blocked_from_reports(self):
        self.client.login(username="rp-donor", password="Test12345!")
        resp = self.client.get(reverse("reports:run", kwargs={"key": "inventory"}))
        self.assertIn(resp.status_code, (403, 302))

    def test_unknown_report_key_denied(self):
        self.client.login(username="rp-admin", password="Test12345!")
        resp = self.client.get(reverse("reports:run", kwargs={"key": "nope"}))
        self.assertIn(resp.status_code, (403, 404))


class ReportDataTests(TestCase):
    def setUp(self):
        self.admin = make_user("rp-admin2", role="ADMIN")
        bt = make_blood_type("O", "NEG")
        comp = make_component("wb", "Whole Blood")
        donor = make_donor(blood_type=bt)
        for _ in range(3):
            make_bag(donor, blood_type=bt, component=comp, status="AVAILABLE")
        self.client.login(username="rp-admin2", password="Test12345!")

    def test_csv_export_contains_rows(self):
        resp = self.client.get(reverse("reports:export", kwargs={"key": "inventory"}))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])
        text = resp.content.decode()
        self.assertIn("generated", text.splitlines()[0])
        self.assertGreaterEqual(len(text.strip().splitlines()), 4)  # header banner + columns + rows

    def test_inventory_report_counts_bags(self):
        resp = self.client.get(reverse("reports:run", kwargs={"key": "inventory"}))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Whole Blood", resp.content.decode())


class ReportPrintPreviewTests(TestCase):
    """The print preview is a real page, not a stub: same rows, role-gated."""

    def setUp(self):
        self.staff = make_user("rp-print-staff", role="STAFF")
        self.donor_user = make_user("rp-print-donor", role="DONOR")
        make_donor(user=self.donor_user)
        bt = make_blood_type("O", "NEG")
        comp = make_component("wb2", "Whole Blood")
        donor = make_donor(blood_type=bt)
        for _ in range(3):
            make_bag(donor, blood_type=bt, component=comp, status="AVAILABLE")
        self.client.login(username="rp-print-staff", password="Test12345!")

    def test_preview_renders_the_formal_document(self):
        resp = self.client.get(reverse("reports:document", kwargs={"key": "inventory"}))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        # Letterhead + confidentiality marking come from the configured org.
        self.assertIn("Blood Bank Management System", body)
        self.assertIn("CONFIDENTIAL", body)
        # The data itself, not just a shell.
        self.assertIn("BB-", body)
        # The preview toolbar offers the other two outputs.
        self.assertIn("doc-toolbar", body)
        self.assertIn(reverse("reports:pdf", kwargs={"key": "inventory"}), body)
        self.assertIn(reverse("reports:export", kwargs={"key": "inventory"}), body)

    def test_preview_carries_the_active_filters_into_the_export_links(self):
        resp = self.client.get(
            reverse("reports:document", kwargs={"key": "inventory"}) + "?status=AVAILABLE")
        body = resp.content.decode()
        self.assertIn("status=AVAILABLE", body)
        # The applied scope is stated on the document itself.
        self.assertIn("Scope applied", body)
        self.assertIn("Available", body)

    def test_export_links_never_inherit_the_page_cursor(self):
        resp = self.client.get(
            reverse("reports:run", kwargs={"key": "inventory"}) + "?page=2")
        body = resp.content.decode()
        self.assertIn("export.pdf", body)
        self.assertNotIn("export.pdf?page=2", body)

    def test_preview_offers_print_pdf_and_csv(self):
        body = self.client.get(
            reverse("reports:document", kwargs={"key": "inventory"})).content.decode()
        # Print needs no URL (the preview is the document), so its presence is a
        # separate assertion from the two real export routes.
        self.assertIn("data-bb-print-now", body)
        self.assertIn("window.print()", body)

    def test_preview_is_staff_only(self):
        self.client.login(username="rp-print-donor", password="Test12345!")
        resp = self.client.get(reverse("reports:document", kwargs={"key": "inventory"}))
        self.assertEqual(resp.status_code, 403)

    def test_admin_only_report_stays_admin_only_for_printing(self):
        resp = self.client.get(reverse("reports:document", kwargs={"key": "audit"}))
        self.assertEqual(resp.status_code, 403)

    def test_unknown_report_key_is_denied_for_printing(self):
        resp = self.client.get(reverse("reports:document", kwargs={"key": "nope"}))
        self.assertIn(resp.status_code, (403, 404))


class ReportPdfTests(TestCase):
    """The PDF is a real file, generated from the same rows as the preview."""

    def setUp(self):
        self.admin = make_user("rp-pdf-admin", role="ADMIN")
        self.staff = make_user("rp-pdf-staff", role="STAFF")
        self.donor_user = make_user("rp-pdf-donor", role="DONOR")
        make_donor(user=self.donor_user)
        bt = make_blood_type("A", "POS")
        comp = make_component("pprc", "Packed Red Cells")
        donor = make_donor(blood_type=bt)
        for _ in range(2):
            make_bag(donor, blood_type=bt, component=comp, status="AVAILABLE")

    def test_pdf_is_a_valid_downloadable_document(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("reports:pdf", kwargs={"key": "inventory"}))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertIn("attachment;", resp["Content-Disposition"])
        self.assertIn(".pdf", resp["Content-Disposition"])
        self.assertTrue(resp.content.startswith(b"%PDF-"), "not a PDF payload")
        self.assertIn(b"%%EOF", resp.content)

    def test_pdf_carries_the_report_title(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("reports:pdf", kwargs={"key": "inventory"}))
        self.assertIn(b"Current Inventory", resp.content)

    def test_pdf_respects_the_same_role_gate(self):
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.get(reverse("reports:pdf", kwargs={"key": "audit"})).status_code, 403)
        self.client.force_login(self.donor_user)
        self.assertEqual(
            self.client.get(reverse("reports:pdf", kwargs={"key": "inventory"})).status_code, 403)

    def test_pdf_filename_is_timestamped(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("reports:pdf", kwargs={"key": "inventory"}))
        self.assertRegex(resp["Content-Disposition"], r"inventory_\d{8}_\d{4}\.pdf")


class ReportMetadataTests(TestCase):
    """Presentation metadata the report centre and the document depend on."""

    def test_every_report_has_a_description_and_a_group(self):
        for key, report in REPORTS.items():
            with self.subTest(report=key):
                self.assertTrue(report.get("description"), f"{key} has no description")
                self.assertTrue(report.get("group"), f"{key} has no group")
                self.assertTrue(report.get("title"))

    def test_wide_reports_are_flagged_landscape(self):
        self.assertTrue(is_landscape("inventory"))
        self.assertTrue(is_landscape("inventory_movements"))
        self.assertFalse(is_landscape("fulfillment_rate"))

    def test_landscape_flags_only_reference_real_reports(self):
        from reports.engine import _LANDSCAPE_REPORTS
        # A typo here would silently print a wide table on portrait paper.
        self.assertLessEqual(_LANDSCAPE_REPORTS, set(REPORTS))

    def test_active_filter_labels_read_as_operator_words(self):
        labels = dict(active_filter_labels(
            "donations", {"status": "COMPLETED", "date_from": "2026-01-05", "q": ""}))
        self.assertEqual(labels["Status"], "Completed")
        self.assertEqual(labels["From"], "05 Jan 2026")
        self.assertNotIn("Search", labels)

    def test_unfiltered_report_still_states_its_scope(self):
        self.assertEqual(active_filter_labels("donors", {}),
                         [("Scope", "All records matching this report")])

    def test_blood_type_filter_is_shown_as_a_group_not_a_pk(self):
        bt = make_blood_type("B", "NEG")
        labels = dict(active_filter_labels("inventory", {"blood_type": str(bt.pk)}))
        self.assertEqual(labels["Blood type"], bt.code)


class DocumentColumnInferenceTests(TestCase):
    """Column alignment/width inference is presentation, and must stay honest."""

    def test_numeric_columns_are_right_aligned(self):
        columns = build_columns(["Name", "Volume (mL)"],
                                [["Whole Blood", "450"], ["Platelets", "60"]])
        self.assertEqual(columns[0].align, "left")
        self.assertEqual(columns[1].align, "right")

    def test_mixed_content_column_is_not_treated_as_numeric(self):
        columns = build_columns(["Volume"], [["450"], ["unknown"]])
        self.assertEqual(columns[0].align, "left")

    def test_placeholder_cells_do_not_break_numeric_detection(self):
        columns = build_columns(["Volume"], [["450"], ["-"]])
        self.assertEqual(columns[0].align, "right")

    def test_widths_sum_to_one_hundred_percent(self):
        columns = build_columns(["A", "B", "C", "D", "E"], [["x" * 30, "y", "z", "w", "v"]])
        self.assertEqual(sum(c.width for c in columns), 100)

    def test_verbose_column_cannot_starve_the_others(self):
        columns = build_columns(["Short", "A very long descriptive column indeed"],
                                [["ab", "x" * 200]])
        # The verbose column is capped, so the short one keeps a real share
        # rather than collapsing to a sliver.
        self.assertGreaterEqual(columns[0].width, 8)
        self.assertGreater(columns[0].width, 0)

    def test_empty_report_still_produces_one_column_per_label(self):
        columns = build_columns(["A", "B"], [])
        self.assertEqual(len(columns), 2)
        self.assertEqual(sum(c.width for c in columns), 100)


class PdfTextSafetyTests(TestCase):
    """The PDF font is WinAnsi; anything else would print as an empty box."""

    def test_arrows_and_typography_are_folded(self):
        self.assertEqual(pdf_safe_text("QUARANTINED \u2192 AVAILABLE"),
                         "QUARANTINED -> AVAILABLE")
        self.assertEqual(pdf_safe_text("a \u201cquoted\u201d value"), 'a "quoted" value')
        self.assertEqual(pdf_safe_text("wait\u2026"), "wait...")

    def test_accented_letters_survive(self):
        self.assertEqual(pdf_safe_text("Jos\xe9 de la Cruz"), "Jos\xe9 de la Cruz")

    def test_unmappable_characters_are_dropped_not_raised(self):
        self.assertIsInstance(pdf_safe_text("\u65e5\u672c"), str)

    def test_none_becomes_empty_string(self):
        self.assertEqual(pdf_safe_text(None), "")
