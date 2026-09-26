# Architecture

## Stack

| Layer | Choice | Why |
|---|---|---|
| Web framework | Django 5.2 (server-rendered templates) | Batteries included, mature auth/ORM, no SPA required |
| Interactivity | HTMX 2.0.4 + Alpine.js 3.14 (CDN) | Server remains the source of truth; light JS |
| Styling | Tailwind CSS 4, built locally (`static/css/input.css` → `tailwind.css`, `npm run build:css`) | `@theme` brand/ink tokens; compiled CSS is committed and cache-busted via `static_v` |
| Charts | Chart.js 4 (CDN), fed by JSON endpoints | Dashboard analytics without a build step |
| DB | SQLite (dev) / PostgreSQL (prod, via `DATABASE_URL`) | Zero-setup dev, real concurrency in prod |

No React/Vue/Angular anywhere. Business rules execute on the server; HTML
fragments and JSON are the only UI transports.

## App map

```
config          settings, root urlconf, wsgi/asgi
core            public landing page at "/" (core:home), role dashboards at
                /dashboard/ (per role), styled-form base, mixins, template
                tags/filters, components, management commands
accounts        custom User (AUTH_USER_MODEL) + roles, auth views, user admin,
                public self-registration (RegistrationRequest +
                RegistrationService: DONOR/REQUESTER only, admin-approval
                gate, login-guard messaging)
audit           append-only AuditLog (immutable model AND queryset)
donors          Donor registry, ScreeningQuestion/DonorScreening/ScreeningResponse,
                DonorEligibilityService (config-driven, fail-safe)
appointments    Appointment scheduling + donor self-booking, slot-conflict guard
donations       Donation state machine, DonationService (create/transition/
                record_collection), CollectionForm
inventory       BloodType/BloodComponent/TestType/BloodBag/TestResult/
                InventoryTransaction/CompatibilityRule;
                InventoryService (locking state machine + release guard),
                CompatibilityService (rule-table-driven, never hard-coded)
requests        Organization/RequesterProfile/BloodRequest/RequestItem/
                Allocation; BloodRequestService (lifecycle, allocation,
                issue, transfuse/return feedback)
notifications   NotificationTemplate/Notification; provider interfaces
                (NotificationProvider → InApp, Django email, Mock/Semaphore SMS);
                NotificationService dispatch/retry; emergency alerts;
                anonymous capability-token response links
rewards         RewardRule/RewardTier/Reward/PointTransaction/DonorReward;
                RewardService ledger (select_for_update), tiers, redemption
reports         config-driven REPORTS registry (~20 reports) + CSV export,
                role-gated
settings_app    SystemSetting + typed accessors; admin-only configuration UI
                (general settings + blood types/components/tests/compatibility)
templates/      all templates (per-app dirs + shared components/)
```

## Layering rule

`views (thin) → services (business rules) → models (constraints)`.

Validation and safety decisions (release guards, state transitions,
permissions, quantity math) live in service classes
(`inventory.services.InventoryService`, `donations.services.DonationService`,
`requests.services.BloodRequestService`, `rewards.services.RewardService`,
`donors.services.DonorEligibilityService`, `notifications.services.
NotificationService`). Views never duplicate them; templates never decide
anything. Confirmation tokens checked in views (`RELEASE`, `YES`, `ISSUE`)
are UX — the service re-checks the actual invariant on every call.

## State machines

Blood bag (`inventory.models.BloodBag.TRANSITIONS`, enforced by
`InventoryService.transition` with `select_for_update`):

```
QUARANTINED → TESTING → AVAILABLE → RESERVED → ISSUED → TRANSFUSED (terminal)
     ↘ REJECTED            ↑  ↑        ↘ EXPIRED → DISCARDED (terminal)
TESTING → QUARANTINED     │  │   ISSUED → RETURNED → AVAILABLE
RETURNED ────────────────┘  └── (cancel allocation / release guard)
Any of QUARANTINED/TESTING/AVAILABLE/RESERVED/RETURNED/EXPIRED/REJECTED → DISCARDED
(ISSUED cannot be discarded directly — it must be TRANSFUSED or RETURNED first)
```

Every status change writes an `InventoryTransaction` ledger row
(previous/new status, actor, reason, reference) plus an audit event.
Release into AVAILABLE from QUARANTINED/TESTING/RETURNED is blocked unless
the release guard passes (below).

Donation: `SCHEDULED → REGISTERED → SCREENING → APPROVED → COLLECTED →
TESTING → RELEASED`, with `DEFERRED / REJECTED / CANCELLED` exits.
Request: `DRAFT → SUBMITTED → UNDER_REVIEW → APPROVED → (PARTIALLY_)FULFILLED`,
exits `REJECTED / CANCELLED / EXPIRED`.
Appointment: `REQUESTED → CONFIRMED → CHECKED_IN → COMPLETED`, exits
`CANCELLED / NO_SHOW` (partial unique constraint prevents double-booking a
donor into an open slot).
Registration (`accounts.models.RegistrationRequest.TRANSITIONS`, enforced by
`RegistrationService.review`): `PENDING → APPROVED | REJECTED`, one-shot —
a reviewed row can never transition again. Approval flips the linked (1:1)
User's `is_active`; rejection requires a non-empty reason, is stored on the
request, and is surfaced to the applicant at login and via in-app/SMS
notification. Self-review of one's own registration is refused.

## Safety-critical guards

1. **Release guard** (`InventoryService._guard_release`): every ACTIVE +
   REQUIRED `TestType` must have a latest `TestResult` that is NON_REACTIVE
   *and verified* (`verified_by` set). If no required tests are configured
   at all, release is blocked — the system refuses to invent a workflow.
2. **Compatibility engine** (`CompatibilityService`): a donor type is
   compatible **only** if an active `CompatibilityRule` row says so for that
   patient type (+ component). No rule ⇒ not compatible. Rules carry an
   `approved_by/approved_at` approval column; unapproved rows are visibly
   marked in the UI.
3. **Row locking**: bag transitions, allocation and reward ledger writes use
   `select_for_update()` inside `transaction.atomic()`.
4. **Eligibility fail-safe**: missing/invalid configured thresholds return
   `REQUIRES_STAFF_REVIEW` rather than guessing a clinical answer.
5. **Immutable audit**: `AuditLog.save()` refuses updates to existing rows,
   `delete()` raises, and `ImmutableAuditQuerySet` blocks bulk
   `.delete()`/`.update()` (queryset deletes bypass per-instance hooks).

## Notifications architecture

Provider interface `NotificationProvider.send(notification) -> (ok, error)`
with implementations: `InAppProvider`, `DjangoEmailProvider` (Django mail),
`MockSMSProvider` (**MOCK — DEVELOPMENT ONLY**, logs, never silently claims a
real send) and `SemaphoreSMSProvider` (live SMS via Semaphore API v4; selected
only when `SMS_PROVIDER=semaphore`, credentials from `SMS_API_KEY` /
`SMS_SENDER_NAME` env vars; fails safe with no HTTP call when unconfigured).
Recipient numbers are normalized to Philippine mobile format `09XXXXXXXXX`.
`core/validators.py` (`normalize_ph_mobile` / `validate_ph_mobile`) is the
single source of truth for mobile format: donor `contact_number` forms and
user `phone` (required on `ProfileForm`/`AdminUserForm`) validate at entry,
and the SMS provider normalizes at dispatch. Existing records with
unregistered/invalid numbers are never bulk-modified — they fail safe at send
time until staff fix them. Emergency donor
alerts include a public UUID capability link
(`/notifications/respond/<token>/`) so anonymous donors can record a
response; responses mean *availability only*, never medical clearance, and
are first-write-wins idempotent.

## Frontend conventions

- Single base layout (`templates/base.html`) with role-aware topbar; centered
  card layout (`base_auth.html`) for login/anonymous token pages.
- Shared components in `templates/components/`: `field.html`,
  `form_errors.html`, `pagination.html`, `status_badge.html`, `card.html`,
  `confirmation.html` (Alpine confirm dialog). **Caution:** `{# … #}` template
  comments must never contain `{`/`%` — Django's comment regex `[^{}]+?`
  leaks such tags as live nodes (this once compiled a self-include into
  `pagination.html`). A test enforces this (`core.tests`).
- Filter/search forms use `hx-get` + `hx-target="#results"` + `hx-push-url`
  with a plain-GET fallback; pagination preserves query strings via the
  `{% querystring %}` tag.
- All form styling applied server-side (`StyledModelForm`/`StyledFormMixin`),
  so non-HTMX rendering looks identical.

## Modal CRUD architecture (dual rendering)

Every Create/Update/Detail/confirm screen renders **two ways from one URL**:
a full page on a plain GET (bookmarks, tests, no-JS fallback) and a modal
fragment when the request carries `HX-Request`. Routing never changes — the
user stays on the current page.

- Helpers live in `core/modals.py`. Views call
  `render_any(request, tpl, ctx, modal_title=…, modal_maxw=…)` for GETs; on
  HTMX it puts `layout = "components/modal_shell.html"` into the context.
  Success responses return `modal_success(request, fallback_redirect=…)` —
  HTMX: empty body + `HX-Trigger: bb:modal-success`; non-HTMX: normal
  302 to the fallback. `ModalFormMixin` wraps the CBV form path.
- Templates start with `{% extends layout|default:"base.html" %}`. Forms add
  conditional `hx-post="…" hx-target="#modal-root" hx-swap="innerHTML"` (and
  `hx-trigger="bb:form-submit"` when guarded by the confirmation dialog);
  Cancel/Back links branch on `{% if layout %}` to `bbCloseModal()`.
- `base.html` hosts `#modal-root` (single mount — opening a second modal
  replaces the first) and listens for `bb:modal-success`: with a `redirect`
  payload it navigates, otherwise it refreshes `#page-content` in place
  (list filters and queued messages survive).
- Triggers: `components/modal_action.html` (or plain `hx-get` links) open
  create/edit/detail/confirm modals; destructive steps use GET-rendered
  confirmation modals posting back to the same URL (e.g.
  `/accounts/users/<pk>/toggle-lock/`, `/appointments/<pk>/status/?to=…`).
  Inline delete flows use the page-level Alpine dialog driven by a
  `bb-confirm-delete` window event (see `settings_app` templates) instead
  of native `confirm()`.
- `modal_smoke.py` (repo root) verifies this contract: fragment-vs-page
  rendering, `bb:modal-success` on hx-post, error re-render, plain-POST 302
  fallback, and role negatives. Rolled back like the other smoke scripts.
