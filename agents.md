# Working notes for coding agents on this repo (agents.md)

Read this before changing anything. It encodes the invariants that are
invisible from a quick skim of the code.

## What this project is

A Django 5.2 templates+HTMX blood bank management system (no SPA frameworks).
Domain, permissions and safety posture are documented in [specs.md](specs.md),
[ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md). Run/test
commands in [README.md](README.md) and [TESTING.md](TESTING.md).

## Hard rules (violating any of these is a defect)

1. **Never invent clinical rules.** Blood-type compatibility, eligibility
   thresholds, required tests, deferral periods, shelf lives, reward values
   — all live in DB tables (`CompatibilityRule`, `SystemSetting`, `TestType`,
   `BloodComponent`, `RewardRule`). If a value is unknown, leave the row
   unconfigured/UNAPPROVED and let the app fail safe
   (`REQUIRES_STAFF_REVIEW`, "not compatible", release blocked). Mark such
   gaps REQUIRES CONFIGURATION / REQUIRES CLARIFICATION in UI/docs.
2. **Server-side only decisions.** Business rules go in service classes
   (`inventory/services.py`, `donations/services.py`,
   `requests/services.py`, `rewards/services.py`, `donors/services.py`),
   never in views/templates/JS. Views may re-check confirmation tokens but
   must not be the only guard.
3. **Audit is append-only.** Anything touching protected state writes
   `audit.services.log(...)`. Do not add update/delete paths to
   `AuditLog`, `InventoryTransaction`, `PointTransaction`.
4. **Never destroy data.** No truncating/resetting commands, no destructive
   migrations, no `--purge` behaviour in management commands without explicit
   operator opt-in. `seed_demo` is idempotent and only inserts.
5. **Role boundaries.** DONOR sees only own data; REQUESTER only own
   organization **and never `channel=WALK_IN` records** (staff-side counter
   intake — excluded in `_visible_requests`, `_get_request_or_403`,
   `awaiting_action_count` and the requester dashboard). A walk-in is a
   **direct clinic request: no approve/reject step** — `validate_walk_in()`
   stores it APPROVED at the counter (`REQUEST_VALIDATED_AT_COUNTER`), and
   `approve()`/`reject()` refuse it. The staff-side **Walk-in Desk**
   (`requests:walk_in_desk`, `requests:walk_in_badge`) is `StaffRequiredMixin` —
   never widen it to REQUESTER/DONOR, and keep its queue on
   `channel=WALK_IN` only. Never add a channel branch to the
   allocation guards: bags still reserve only against APPROVED. STAFF cannot
   touch critical configuration (`AdminRequiredMixin`); permission negatives
   stay tested (see TESTING.md matrix) whenever routes change.
6. **External providers**: default SMS provider stays `MockSMSProvider`
   (labelled MOCK — DEVELOPMENT ONLY). `SemaphoreSMSProvider` is live-capable
   but only when `SMS_PROVIDER=semaphore` + `SMS_API_KEY` are set in the
   environment; it must fail safe (no HTTP call) when unconfigured. Don't
   fake delivery success in UI copy.
7. **Secrets** only via env / `.env` (git-ignored). `.env.example` documents
   every variable. Never commit credentials, never hard-code fallbacks that
   look production-safe.
8. **Printable documents go through `core/documents.py`.** Never hand-roll a
   printable page or a PDF in a view/template. Build a `DocumentSpec` and let
   `PrintableDocumentMixin` render the preview and the PDF from it, so the
   printed page, the preview and the download are always the same document.
   `templates/documents/document.html` is written to the xhtml2pdf CSS subset
   on purpose — **no flexbox, no grid, no CSS variables, no external
   stylesheets** there; adding one silently breaks the PDF. Reportlab's fonts are
   WinAnsi, so any character a user can type must go through `pdf_safe_text()`
   for the PDF or it prints as an empty box. A document is a record: it must
   never invent a figure, and a truncated listing must say it is truncated
   (see `STATEMENT_LIST_LIMIT`). Export endpoints inherit the *same* role gate
   as the page they came from — printing is never a way around `REPORTS[key]["roles"]`.

## Django/templates gotchas that bit us

- `{# … #}` comments must be **single-line** and must not contain `{` or `%`:
  Django's comment regex is `[^{}]*?`-based and rejects newlines/braces, so
  such comments either leak their text as visible page content (multi-line)
  or compile inner tags as LIVE nodes (braces — this once self-included
  `pagination.html` → RecursionError at render time, invisible to
  `get_template()` compile checks). A test now scans all templates.
- A plain `forms.Form` must not inherit `StyledModelForm` (ValueError at POST
  — this 500'd every collection submit).
- `settings.LOGIN_URL` is a URL **name**; middleware/path comparisons must
  `resolve_url()` it first (name-vs-path caused a login redirect loop).
- `/` is the PUBLIC landing page (`core:home`), allow-listed via
  `PUBLIC_PATHS_EXACT` in `core/middleware.py` — never add bare `"/"` to the
  `PUBLIC_PATHS` startswith tuple (it would match every URL). The role
  dashboard lives at `/dashboard/`; login always lands on `core:dashboard`
  (honouring `?next=`), so landing ↔ login ↔ dashboard stays consistent.
- `QuerySet.delete()` bypasses model `.delete()` overrides — immutability
  also needs a custom `QuerySet`/manager (see `audit.models`).
- Model `save(update_fields=[...])` lists must not include fields the model
  lacks (`updated_at` on Notification tripped a retry-flow crash).
- Windows console (cp1252): management-command stdout must stay ASCII;
  `→` etc. only in files/DB.
- `{% url … as name %}` may not start with `_` (Django rejects
  underscore-prefixed variable names at compile — `check_templates.py`
  catches it).
- Inside a form whose `@submit.prevent` opens the confirmation dialog, the
  confirm button must be `type="button"` + `form.submit()` — a `type="submit"`
  button gets swallowed by that handler and the guarded action never posts.
- Test `Client` used outside the runner needs `settings.ALLOWED_HOSTS +=
  ["testserver"]`.
- **The public landing page (`core/home.html`) is standalone** — it does NOT
  extend `base.html`. Its GSAP/Three.js layer lives in
  `static/js/landing-scene.js` (ES module, imports three from CDN) and
  `static/js/landing-anim.js` (classic, window.gsap). Decorative only — no
  business logic in JS. Invariants: every element visible without JS
  (animations use `gsap.from` only), counters start server-rendered,
  `prefers-reduced-motion` → single static frame, WebGL/CDN failure → CSS
  gradient fallback (never a broken page).
- **Auth shell must stay scroll-safe**: `base_auth.html` centres with a
  `min-h-screen flex flex-col justify-center` wrapper and normal document
  flow — NOT `body{flex items-center}` + `overflow-hidden` (that combination
  clips tall cards such as `accounts/register.html` on desktop and cuts the
  footer off on short viewports). Decorative orbs live in a
  `fixed inset-0 overflow-hidden` layer so they can't create horizontal
  scroll; card padding/type scale via `sm:` variants; width override uses the
  `{% block auth_width %}` block (register uses `max-w-lg`).
- **Tailwind CSS 4 is built locally, not via CDN.** Source:
  `static/css/input.css` (holds the `@theme` brand/ink tokens + base styles);
  output: `static/css/tailwind.css`, linked by `base.html`/`base_auth.html`/
  `errors/error.html`. After adding/renaming utility classes in templates or
  Python (`core/forms.py` `INPUT_CLASSES`), run `npm run build:css` (or keep
  `npm run watch:css` running) — the compiled file is committed, but pages
  will show stale/missing styles until it is rebuilt. The `<link>` uses the
  `static_v` tag (core_extras), which appends the file's mtime+size as `?v=`
  so browsers pick up rebuilds without a hard refresh — keep using it for
  built assets. `settings.STORAGES`
  uses the whitenoise manifest backend only when `DEBUG=False` and not
  testing (otherwise `{% static %}` would demand a fresh collectstatic).
- **`templates/documents/document.html` is PDF-constrained.** It is the only
  template that must stay inside the xhtml2pdf CSS subset, so: no `<link>` to an
  external stylesheet, no Tailwind classes, no flexbox/grid/CSS variables, and
  every colour written out explicitly. Screen-only styling goes inside
  `{% if chrome %}`, which is never set for the PDF — that is what makes a
  toolbar structurally unable to reach paper. `pdf:pagenumber` /
  `pdf:pagecount` inside `#doc_header`/`#doc_footer` are xhtml2pdf-only and must
  not be moved into a normal element. Adding a report or a statement means
  adding rows/spec fields, not editing this template.

## Verification workflow (run before declaring anything done)

```
npm run build:css                 # rebuild static/css/tailwind.css after class changes
python manage.py check
python manage.py test                 # must stay OK
python check_templates.py             # all templates compile
python check_e2e.py                   # 4-role GET walk + negative access
python check_e2e_post.py              # POST workflows (rolls back)
python modal_smoke.py                 # modal/HX-Request contract (rolls back)
python check_documents.py             # every report + the inventory statement:
                                      #   preview 200, real %PDF- bytes, 403 for
                                      #   the roles that must not have them
```

`check_e2e*.py` and `check_documents.py` require a seeded dev DB
(`manage.py seed_demo`) and never modify it (POST smoke is wrapped in a
rolled-back atomic block). If you add a page/route/permission, extend those
scripts AND the matching app `tests.py` (especially negative-access assertions).
`check_documents.py` is the one to extend whenever a new report or a new
printable document is added — it is what proves the PDF is a real PDF.

## Style

- Views thin; services with typed results (`EligibilityResult` dataclass is
  the model).
- Forms: inherit `StyledModelForm` / `StyledFormMixin` (core/forms.py) so
  Tailwind classes are applied server-side; render fields via
  `components/field.html`.
- Destructive/guarded actions use the Alpine `components/confirmation.html`
  dialog + a server-checked confirm token word (`RELEASE`, `ISSUE`, `YES`).
- Templates: per-app folders under `templates/`, shared parts in
  `templates/components/`; HTMX filter forms target `#results` with
  `hx-push-url` and plain-GET fallback; pagination uses `{% querystring %}`.
- Status enums are DB strings — extend state machines by editing the
  `TRANSITIONS` dicts and their service guards, then update
  DATABASE/ARCHITECTURE docs and tests together.
- **Modal CRUD (all modules)**: same URL renders full page on plain GET and
  modal fragment on `HX-Request` — see "Modal CRUD architecture" in
  ARCHITECTURE.md. Views use `core/modals.py` (`render_any` for GETs,
  `modal_success` for every POST response); templates
  `{% extends layout|default:"base.html" %}`, add conditional
  `hx-post/hx-target="#modal-root"/hx-swap="innerHTML"` to forms, and
  branch Cancel/Back on `{% if layout %}` to `bbCloseModal()`. Guarded
  modal forms pair `hx-trigger="bb:form-submit"` with
  `components/confirmation.html` (which dispatches that event when
  `layout` is set). Never `confirm()` in templates — use the
  `bb-confirm-delete` page dialog (settings templates) or a GET-rendered
  confirmation modal.
- **Modal CRUD has one exception: heavy detail screens are pages.** If a screen
  has **forms plus more than one action**, it is a working surface, not a
  preview — keep it a full page. `inventory:bag_detail` is the case in point:
  `BagDetailView` uses plain `render()` (NOT `render_any`), its template extends
  `base.html` with no `{% if layout %}` branches, and every bag link is a plain
  anchor (bag list, ledger, dashboard, compat check, request detail, donation
  detail, staff dashboard). Two reasons, both load-bearing: a modal buries the
  test-result form and guarded release actions in a nested scroll box and loses
  the surrounding stock context; and rendering the full document unconditionally
  means a reintroduced `hx-get` injects a whole `<html>` into `#modal-root` and
  fails loudly instead of silently half-rendering.
  `inventory.tests.BagDetailIsAPageTests` pins it — extend that test if you add
  another heavy screen.
