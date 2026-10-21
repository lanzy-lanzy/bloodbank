"""Formal printable documents (print preview + PDF) shared by every module.

The reporting centre and the inventory statement both render through ONE
template (``documents/document.html``) so a printed page, a print preview and a
downloaded PDF can never drift apart.

Why a server-side PDF and not only ``window.print()``: ``window.print()`` hands
the operator a browser dialog whose "Save as PDF" step is a second, unnamed
path, and it prints whatever app chrome happens to be on screen. Here the same
HTML is converted to a real PDF file with xhtml2pdf (pure Python, no native
pango/cairo dependency), so the download is a genuine document with a letterhead
and page numbering.

The template is deliberately written in the intersection of browser-print CSS
and the xhtml2pdf subset: no flexbox/grid, table-based layout, explicit colours.
Anything outside that subset would either be dropped by the PDF engine or shift
the print layout, so it does not belong here.

Layout knobs are data (``DocumentSpec``), never template edits: a module decides
*what* the document says, this module decides how it is typeset.
"""
import io
import logging
import re
from dataclasses import dataclass, field, replace

from django.http import HttpResponse
from django.template.loader import render_to_string

logger = logging.getLogger("bloodbank.documents")

# Paper sizes xhtml2pdf understands for `@page { size: ... }`.
PAGE_SIZES = ("A4", "LETTER", "LEGAL")

# A table at least this wide switches to landscape automatically: eight columns
# on portrait A4 wrap every cell into an unreadable sliver.
LANDSCAPE_FROM_COLUMNS = 8

# xhtml2pdf renders through reportlab's built-in Type1 fonts, which cover
# WinAnsi (cp1252) only. Anything outside that subset is emitted as a blank
# box, so the *data* of a document - donor names, reasons, free text typed by
# staff - is folded to safe equivalents for the PDF. These are presentation
# characters, never clinical ones, so substituting them loses no meaning.
_PDF_UNSAFE = {
    "→": "->", "←": "<-", "↑": "^", "↓": "v",   # arrows
    "−": "-", "–": "-", "—": "-",               # minus / dashes
    "•": "*", "·": "-", "‣": "-",               # bullets
    "“": '"', "”": '"', "‘": "'", "’": "'",     # curly quotes
    "…": "...", "′": "'", "″": '"', "€": "EUR", "£": "GBP",
    "≤": "<=", "≥": ">=", "≠": "!=", "±": "+/-",
    " ": " ", " ": " ", " ": " ",        # exotic spaces
    "✅": "", "❌": "", "⚠": "",                    # emoji occasionally pasted into a reason
}
_PDF_UNSAFE_RE = re.compile("|".join(re.escape(ch) for ch in sorted(_PDF_UNSAFE, key=len, reverse=True)))


def pdf_safe_text(value) -> str:
    """Fold ``value`` into the character set the PDF font can actually draw."""
    if value is None:
        return ""
    text = _PDF_UNSAFE_RE.sub(lambda m: _PDF_UNSAFE[m.group(0)], str(value))
    return text.encode("cp1252", "replace").decode("cp1252", "replace")


def pdf_safe_rows(rows) -> list:
    """``pdf_safe_text`` applied to every cell, preserving the row shape."""
    return [[pdf_safe_text(cell) for cell in row] for row in rows]


class DocumentRenderError(Exception):
    """The PDF backend is missing or refused the document."""


@dataclass(frozen=True)
class DocumentColumn:
    """One printed column: its heading, alignment and relative width (%)."""

    label: str
    align: str = "left"  # "right" for figures, so the decimal points line up
    width: int = 0

    @property
    def css_align(self) -> str:
        return "right" if self.align == "right" else "left"


@dataclass
class DocumentSpec:
    """Everything needed to typeset one formal document.

    ``meta`` is the small label/value grid under the title (generated on/by,
    scope, record count). ``summary`` is the optional row of headline figures.
    Both are presentation-neutral: the values are computed by the calling module,
    so no clinical or operational figure is ever invented here.
    """

    title: str
    doc_type: str = "REPORT"
    subtitle: str = ""
    reference: str = ""
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    meta: list = field(default_factory=list)  # [(label, value), ...]
    summary: list = field(default_factory=list)  # [(label, value), ...]
    notes: list = field(default_factory=list)
    empty_text: str = "No records matched the selected criteria."
    show_signatures: bool = True
    prepared_by: str = ""
    prepared_role: str = "Blood Bank Staff"
    toolbar: dict = None  # {"pdf": url, "csv": url, "back": url} — preview actions
    page_size: str = "A4"
    landscape: bool = False
    pdf_filename: str = "document.pdf"

    def resolved_landscape(self) -> bool:
        return bool(self.landscape) or len(self.columns) >= LANDSCAPE_FROM_COLUMNS

    def resolved_page_size(self) -> str:
        size = (self.page_size or "A4").upper()
        return size if size in PAGE_SIZES else "A4"



# --- Column inference ------------------------------------------------------------
_NUMERIC_RE = re.compile(r"^[+-]?[\d,]*\.?\d+\s*%?$")
# Placeholders the report engine writes for "no value"; never treated as figures.
_EMPTY_CELLS = {"", "-", "--", "—", "–", "n/a", "N/A", "None", "null"}


def _looks_numeric(value) -> bool:
    return bool(_NUMERIC_RE.match(str(value).strip()))


def build_columns(labels, rows) -> list:
    """Derive alignment + relative widths from the data itself.

    A column whose populated cells are all numeric is right-aligned so digits
    line up; the rest stay left-aligned. Widths are proportional to the widest
    rendered value (heading included) and normalised to whole percentages, which
    is what stops a formal table from collapsing "Bag ID" to a sliver just
    because it happens to hold short codes.
    """
    rows = list(rows or [])
    columns = []
    for index, label in enumerate(labels):
        values = [
            str(row[index]) for row in rows
            if index < len(row) and str(row[index]).strip() not in _EMPTY_CELLS
        ]
        numeric = bool(values) and all(_looks_numeric(v) for v in values)
        longest = max([len(str(label))] + [len(v) for v in values]) if values else len(str(label))
        # Clamp so one verbose column cannot starve the rest.
        columns.append(DocumentColumn(label=str(label),
                                      align="right" if numeric else "left",
                                      width=max(6, min(longest, 42))))
    total = sum(c.width for c in columns) or len(columns) or 1
    scaled = [max(5, round(c.width * 100 / total)) for c in columns]
    drift = 100 - sum(scaled)
    if drift and scaled:
        # Absorb rounding drift into the widest column so the row totals 100%.
        widest = scaled.index(max(scaled))
        scaled[widest] = max(5, scaled[widest] + drift)
    return [replace(c, width=w) for c, w in zip(columns, scaled)]


def build_document(*, title, columns, rows, **kwargs) -> DocumentSpec:
    """A ``DocumentSpec`` with alignment/width inference already applied.

    ``page_size`` defaults to the deployment's ``REPORT_PAPER`` setting so the
    whole institution prints on the same stock without every caller repeating
    it. Wide tables go landscape on their own (see ``DocumentSpec``).
    """
    from django.conf import settings
    kwargs.setdefault("page_size", getattr(settings, "REPORT_PAPER", "A4"))
    return DocumentSpec(title=title, columns=build_columns(columns, rows), rows=list(rows), **kwargs)


# --- Form documents --------------------------------------------------------------
# A report is a table of rows; an official form is a page of labelled boxes,
# tick tables and signature lines. Both are printable documents, so both live
# here and share the preview/PDF path: a module describes the content, this
# module typesets it. The form template is written to the same xhtml2pdf CSS
# subset (tables, explicit colours, no flexbox/grid/variables).

FORM_SECTION_KINDS = ("fields", "table")


@dataclass(frozen=True)
class FormField:
    """One box on a form: a small label above the value it holds."""

    label: str
    value: str = ""
    width: int = 0  # % of its row; 0 = share the remainder equally
    align: str = "left"


@dataclass(frozen=True)
class FormRow:
    """One line of a fields block: an optional gutter label plus its boxes."""

    label: str = ""
    label_width: int = 17  # % of the line taken by the gutter label
    cells: tuple = ()

    def resolved_cells(self) -> tuple:
        """Cells with the zero-width ones sharing the space the gutter leaves."""
        fixed = sum(cell.width for cell in self.cells)
        free = [cell for cell in self.cells if not cell.width]
        if not free:
            return self.cells
        gutter = self.label_width if self.label else 0
        share = max(1, (100 - gutter - fixed) // len(free))
        return tuple(cell if cell.width else replace(cell, width=share)
                     for cell in self.cells)


@dataclass(frozen=True)
class FormSection:
    """A titled block of a form document.

    ``kind="fields"`` renders ``rows``. ``kind="table"`` renders ``columns``
    over ``items`` - each item is ``(group_heading, (cell, ...))`` where an
    empty heading means "no group rule above this row" and each cell is
    ``(text, alignment)``. Alignment ``"box"`` draws a tick box around the
    text, so an unanswered cell still prints an empty box to tick by hand.
    A tick in a YES/NO table is a cell the caller already filled in ("X" or
    ""), so this module never decides what an answer means.
    """

    kind: str = "fields"
    title: str = ""
    subtitle: str = ""
    note: str = ""
    rows: tuple = ()
    columns: tuple = ()  # tuple[DocumentColumn]
    items: tuple = ()


@dataclass
class FormDocumentSpec:
    """Everything needed to typeset one official form.

    ``cue`` is a small, fixed panel shape used by the confidential unit
    exclusion block:
        {"title", "body": [paragraph], "question",
         "options": [(label, checked)], "fields": [(label, value)],
         "footnote"}
    It is a dict rather than another dataclass because it is one known panel
    with a handful of optional parts, not a layout primitive.
    """

    title: str
    doc_type: str = "FORM"
    subtitle: str = ""
    reference: str = ""
    header: tuple = ()      # tuple[FormField] - top strip of reference boxes
    sections: tuple = ()
    cue: dict = None
    signatures: tuple = ()  # tuple[(role, printed_name, date_label, date_value)]
    notes: list = field(default_factory=list)
    footer_note: str = ""
    toolbar: dict = None
    page_size: str = "A4"
    pdf_filename: str = "form.pdf"

    def resolved_page_size(self) -> str:
        size = (self.page_size or "A4").upper()
        return size if size in PAGE_SIZES else "A4"

    def resolved_header(self) -> tuple:
        """Header boxes with the zero-width ones sharing the full line."""
        fixed = sum(cell.width for cell in self.header)
        free = [cell for cell in self.header if not cell.width]
        if not free:
            return self.header
        share = max(1, (100 - fixed) // len(free))
        return tuple(cell if cell.width else replace(cell, width=share)
                     for cell in self.header)

    def signature_width(self) -> int:
        """Width of one signature block, so the blocks split the line evenly."""
        count = len(self.signatures) or 1
        return 100 // count


def _fold_cell(cell: FormField) -> FormField:
    return replace(cell, label=pdf_safe_text(cell.label), value=pdf_safe_text(cell.value))


def _fold_section(section: FormSection) -> FormSection:
    return replace(
        section,
        title=pdf_safe_text(section.title),
        subtitle=pdf_safe_text(section.subtitle),
        note=pdf_safe_text(section.note),
        rows=tuple(replace(row, label=pdf_safe_text(row.label),
                           cells=tuple(_fold_cell(cell) for cell in row.cells))
                   for row in section.rows),
        columns=tuple(replace(column, label=pdf_safe_text(column.label))
                      for column in section.columns),
        items=tuple((pdf_safe_text(heading),
                     tuple((pdf_safe_text(value), align) for value, align in cells))
                    for heading, cells in section.items),
    )


def _fold_cue(cue) -> dict:
    if not cue:
        return None
    return {
        "title": pdf_safe_text(cue.get("title", "")),
        "body": [pdf_safe_text(paragraph) for paragraph in cue.get("body", [])],
        "question": pdf_safe_text(cue.get("question", "")),
        "options": [(pdf_safe_text(label), bool(checked))
                    for label, checked in cue.get("options", [])],
        "fields": [(pdf_safe_text(label), pdf_safe_text(value))
                   for label, value in cue.get("fields", [])],
        "footnote": pdf_safe_text(cue.get("footnote", "")),
    }


def form_pdf_spec(spec: FormDocumentSpec, **overrides) -> FormDocumentSpec:
    """A copy of ``spec`` with every printed string folded to the PDF charset."""
    return replace(
        spec,
        toolbar=None,
        title=pdf_safe_text(spec.title),
        subtitle=pdf_safe_text(spec.subtitle),
        reference=pdf_safe_text(spec.reference),
        header=tuple(_fold_cell(cell) for cell in spec.header),
        sections=tuple(_fold_section(section) for section in spec.sections),
        cue=_fold_cue(spec.cue),
        signatures=tuple((pdf_safe_text(role), pdf_safe_text(name),
                          pdf_safe_text(date_label), pdf_safe_text(date_value))
                         for role, name, date_label, date_value in spec.signatures),
        notes=[pdf_safe_text(note) for note in spec.notes],
        footer_note=pdf_safe_text(spec.footer_note),
        **overrides,
    )


def document_template(spec) -> str:
    """The template that typesets this spec (reports vs official forms)."""
    if isinstance(spec, FormDocumentSpec):
        return "documents/form_document.html"
    return "documents/document.html"


# --- Rendering -------------------------------------------------------------------
def _site(request) -> dict:
    """Institution letterhead, read from the same context processor the UI uses.

    A printed document must always carry the *configured* organization name and
    contact details, never a hard-coded string baked into this module.
    """
    from core.context_processors import site_settings
    return site_settings(request)


def document_context(request, spec: DocumentSpec) -> dict:
    """Template context for one document."""
    site = _site(request)
    return {
        "doc": spec,
        "ORG_NAME": site["ORG_NAME"],
        "ORG_ADDRESS": site["ORG_ADDRESS"],
        "ORG_CONTACT": site["ORG_CONTACT"],
    }


def render_document(request, spec, *, chrome: bool = True) -> str:
    """HTML for ``spec``.

    ``chrome=True`` adds the on-screen desk backdrop and the action toolbar,
    i.e. the print-preview page. ``chrome=False`` returns the bare document,
    which is what the PDF backend consumes — a toolbar must never reach paper.
    """
    return render_to_string(
        document_template(spec),
        {**document_context(request, spec), "chrome": chrome},
        request=request,
    )


class _QuietCssWarnings(logging.Filter):
    """Drop xhtml2pdf's per-property "does not implement" chatter.

    The document stylesheet sticks to the supported subset on purpose, so these
    warnings are expected noise rather than a signal. Real failures still
    surface as ``DocumentRenderError`` and as ERROR-level log records.
    """

    def filter(self, record):
        return "does not implement" not in record.getMessage()


def render_document_pdf(request, spec) -> bytes:
    """Convert ``spec`` to PDF bytes.

    Raises ``DocumentRenderError`` when the backend is unavailable or refuses
    the document, so the view can degrade to the browser print path instead of
    handing the operator a corrupt download.
    """
    try:
        from xhtml2pdf import pisa
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise DocumentRenderError(
            "PDF export needs the 'xhtml2pdf' package (pip install -r requirements.txt)."
        ) from exc

    safe_spec = form_pdf_spec(spec) if isinstance(spec, FormDocumentSpec) else pdf_spec(spec)
    html = render_document(request, safe_spec, chrome=False)
    for name in ("xhtml2pdf", "xhtml2pdf.parser", "xhtml2pdf.document"):
        target = logging.getLogger(name)
        if not any(isinstance(f, _QuietCssWarnings) for f in target.filters):
            target.addFilter(_QuietCssWarnings())

    buffer = io.BytesIO()
    try:
        status = pisa.CreatePDF(io.BytesIO(html.encode("utf-8")), dest=buffer)
    except Exception as exc:  # noqa: BLE001 - any backend failure must degrade safely
        logger.exception("PDF render failed for %s", spec.title)
        raise DocumentRenderError("The document could not be converted to PDF.") from exc
    if status.err:
        logger.error("PDF render reported %s error(s) for %s", status.err, spec.title)
        raise DocumentRenderError("The document could not be converted to PDF.")
    return buffer.getvalue()


def pdf_spec(spec: DocumentSpec, **overrides) -> DocumentSpec:
    """A copy of ``spec`` prepared for the PDF backend.

    Two things change: the on-screen toolbar is dropped (it must never reach
    paper), and every piece of operator-supplied text is folded to the cp1252
    subset the built-in PDF fonts can draw, so a name or a reason containing an
    arrow or an accented character renders as text rather than a blank box.
    Column alignment is *not* recomputed: folding cannot turn a text column into
    a numeric one, and keeping the inference identical is what makes the
    preview and the PDF line up.
    """
    return replace(
        spec,
        toolbar=None,
        title=pdf_safe_text(spec.title),
        subtitle=pdf_safe_text(spec.subtitle),
        reference=pdf_safe_text(spec.reference),
        notes=[pdf_safe_text(note) for note in spec.notes],
        prepared_by=pdf_safe_text(spec.prepared_by),
        meta=[(pdf_safe_text(label), pdf_safe_text(value)) for label, value in spec.meta],
        summary=[(pdf_safe_text(label), pdf_safe_text(value)) for label, value in spec.summary],
        columns=[replace(c, label=pdf_safe_text(c.label)) for c in spec.columns],
        rows=pdf_safe_rows(spec.rows),
        **overrides,
    )


def pdf_response(request, spec) -> HttpResponse:
    """A downloadable ``application/pdf`` named after the document."""
    response = HttpResponse(render_document_pdf(request, spec), content_type="application/pdf")
    filename = spec.pdf_filename if spec.pdf_filename.endswith(".pdf") else f"{spec.pdf_filename}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

