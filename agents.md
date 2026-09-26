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
   organization; STAFF cannot touch critical configuration
   (`AdminRequiredMixin`); permission negatives stay tested (see TESTING.md
   matrix) whenever routes change.
6. **External providers**: default SMS provider stays `MockSMSProvider`
   (labelled MOCK — DEVELOPMENT ONLY). `SemaphoreSMSProvider` is live-capable
   but only when `SMS_PROVIDER=semaphore` + `SMS_API_KEY` are set in the
   environment; it must fail safe (no HTTP call) when unconfigured. Don't
   fake delivery success in UI copy.
7. **Secrets** only via env / `.env` (git-ignored). `.env.example` documents
   every variable. Never commit credentials, never hard-code fallbacks that
   look production-safe.

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

## Verification workflow (run before declaring anything done)

```
npm run build:css                 # rebuild static/css/tailwind.css after class changes
python manage.py check
python manage.py test                 # 145 tests — must stay OK
python check_templates.py             # all templates compile
python check_e2e.py                   # 116-page GET walk (4 roles + anonymous)
python check_e2e_post.py              # 69-assert POST workflows (rolls back)
python modal_smoke.py                 # modal/HX-Request contract (rolls back)
```

`check_e2e*.py` require a seeded dev DB (`manage.py seed_demo`) and never
modify it (POST smoke is wrapped in a rolled-back atomic block). If you add
a page/route/permission, extend those scripts AND the matching app `tests.py`
(especially negative-access assertions).

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
