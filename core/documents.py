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


def render_document(request, spec: DocumentSpec, *, chrome: bool = True) -> str:
    """HTML for ``spec``.

    ``chrome=True`` adds the on-screen desk backdrop and the action toolbar,
    i.e. the print-preview page. ``chrome=False`` returns the bare document,
    which is what the PDF backend consumes — a toolbar must never reach paper.
    """
    return render_to_string(
        "documents/document.html",
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


def render_document_pdf(request, spec: DocumentSpec) -> bytes:
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

    html = render_document(request, pdf_spec(spec), chrome=False)
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


def pdf_response(request, spec: DocumentSpec) -> HttpResponse:
    """A downloadable ``application/pdf`` named after the document."""
    response = HttpResponse(render_document_pdf(request, spec), content_type="application/pdf")
    filename = spec.pdf_filename if spec.pdf_filename.endswith(".pdf") else f"{spec.pdf_filename}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

