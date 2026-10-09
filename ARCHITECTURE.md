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
                issue, transfuse/return feedback, walk-in desk resolution)
notifications   NotificationTemplate/Notification; provider interfaces
                (NotificationProvider → InApp, Django email, Mock/Semaphore SMS);
                NotificationService dispatch/retry; emergency alerts;
                anonymous capability-token response links
rewards         RewardRule/RewardTier/Reward/PointTransaction/DonorReward;
                RewardService ledger (select_for_update), tiers, redemption
reports         config-driven REPORTS registry (~20 reports) + CSV export,
                print preview and server-rendered PDF, role-gated
core            shared shell: role mixins, modal CRUD, template tags,
                and `documents.py` — the formal printable-document engine
                (DocumentSpec → HTML print preview / PDF via xhtml2pdf)
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
Approving a REQUESTER additionally links the account to an organization
(`RegistrationService._link_requester_organization`): the submitted
`organization_name` is matched case-insensitively against `Organization`, or
created (`org_type=OTHER`, correctable under Organizations) when unknown, and
a `RequesterProfile` is attached — otherwise the new user would sign in to a
dead-end dashboard. A blank name or a pre-existing profile is left alone, so
the "contact staff" banner stays the fail-safe.

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

Request lifecycle decisions are pushed back to the requesting organization as
in-app notifications from the service layer (`BloodRequestService._notify_requester`
on `approve` → `request_approved`, `reject` → `request_rejected` with the reason,
and on the transition to `FULFILLED` → `request_fulfilled`). The recipient is
`created_by`, so a request saved as a draft by staff notifies nobody. Notification
failure is logged and swallowed: a missing template must never roll back or block
the clinical decision itself.

## Request → inventory bridge

A request consumes stock only through `Allocation`. `AllocateForm` (requests/forms.py)
offers `AVAILABLE` bags of the requested component whose blood type is compatible per
`CompatibilityService`, restricted to unexpired bags (`expires_at__gt now`) so the
dropdown can never list a bag the Compatibility Report counts as unavailable — the
report, the dropdown and the reservation guard all read the same rule set. The detail
page renders one of four explicit states per outstanding item: a bag selector plus
"N compatible bag(s) on hand · M unit(s) open to reserve", **All requested units are
reserved** (nothing left to reserve), **No compatible bag in stock** with the shortfall
(and a pointer to the Emergency Donor Pool for emergency requests), or "approve request
first". A rejected POST says which of those it is rather than echoing a generic "select a bag".

**Reservation quota.** Three counters, deliberately distinct:
`outstanding` (`quantity - fulfilled_quantity`, what is still owed), `units_reserved`
(bags held `RESERVED` but not yet issued) and `reservable_units` (`outstanding` minus
`units_reserved`, what may still be reserved). `allocate_bag()` refuses once
`reservable_units` hits 0, re-checking on a `select_for_update()` copy of the item row
inside its transaction — checking only `outstanding` let two staff reserve stock for the
same unit. `reconcile_inventory` reports the fallout it used to be able to create
(`[OVER-RESERVATION]`, `[FULFILLMENT]`, `[ORPHAN]` reservations on closed requests).

## Walk-in intake (patients at the counter)

Not every request arrives through a requester account: a patient can walk up to
the blood bank directly. `BloodRequest.channel` records which it was —
`ORGANIZATION` (default, requester self-service) or `WALK_IN` (staff-logged).
Only ADMIN/STAFF see the channel control: `BloodRequestForm(staff=…)` pops the
channel and contact fields for everyone else, and `RequestCreateView` derives
the owning organization server-side, so a forged `channel=WALK_IN` POST from a
requester is simply absent from its form and the record stays an
organization-channel row for their own organization.

Walk-in rows still need an owning `Organization` (the model, reports and audit
trail all key on it), so they are booked against the desk named by the
`walk_in_organization_id` setting — `BloodRequestService.walk_in_organization()`
resolves it and returns `None` when unset or inactive, in which case the create
form refuses with a **REQUIRES CONFIGURATION** error rather than guessing an
organization. The desk is excluded from the ordinary organization dropdown and
from being picked on the organization channel, so a record can never be
mislabelled in either direction.

**No approval queue.** A walk-in is a direct clinic request, so the staff member
at the counter *is* the authority — there is nobody to wait for.
`BloodRequestService.validate_walk_in()` marks the new row `APPROVED` immediately
(`approved_by` = the creating staff user) and writes its own append-only audit
action, `REQUEST_VALIDATED_AT_COUNTER`. This removes a form, not a guard: bags
still only reserve against `APPROVED` status and issuing stays a separate
confirmed step, so walk-in and organization requests share identical inventory
guarantees. `approve()` and `reject()` raise `RequestError` for a walk-in row
(the UI hides those actions; the service refuses a forged POST anyway), and
`submit()` routes a walk-in draft through `validate_walk_in()` so a walk-in can
never land in `SUBMITTED` by any path. `_notify_requester()` returns early for
walk-ins — there is no requester account to notify, and the desk organization
owns no requester logins. Closing an unservable walk-in uses **cancel**, which
exists for every channel.

**Scoping invariant:** `channel=WALK_IN` is a staff-side record. It is excluded
from `_visible_requests`, `_get_request_or_403` (403 even for a member of the
desk organization), `awaiting_action_count` and the requester dashboard — a
requester must never read a walk-in patient's row. Because there is no
requester account, staff also close the loop: the detail page shows *Mark
Transfused* / *Return Unused* for an issued walk-in allocation, and
`mark_transfused`/`return_allocation` accept the staff actor. Inventory effects
are identical to any other request — reservations, issues and the
open-demand panel do not branch on channel.

**Walk-in Desk workspace** (`/requests/walk-ins/`, `requests:walk_in_desk`;
badge at `requests:walk_in_badge`): the counter's own page, so staff do not have
to find walk-ins in the general request list. `WalkInDeskView` is
`StaffRequiredMixin`-only and renders `BloodRequestService.walk_in_queue()`
(channel-filtered, ordered by `required_by` then newest) through the shared
`requests/_table.html` — one table shape, with the counter contact shown under
the organization. Scope tabs (Open / Closed / All) swap `#results` over HTMX and
fall back to a plain GET; the stat cards read `walk_in_action_count()` (drafts
plus approved records still owing units) and the open/closed splits.
*Log Walk-in Request* opens the existing create modal with
`?channel=WALK_IN`, which preselects the walk-in card server-side through the
form's `initial` — the channel decision stays in the service/form, and the JS
only toggles visibility. With no desk configured the page states **REQUIRES
CONFIGURATION** and links to System Settings instead of refusing silently.

## Inventory awareness (dashboard + badge)

`BloodRequestService.open_demand()` is the read-only demand side of that bridge: one row
per outstanding item on an `APPROVED`/`PARTIALLY_FULFILLED` request, with free compatible
stock and shortage measured against `reservable_units` (a reservation has already left the
`AVAILABLE` pool, so a half-reserved item must not read as a hard shortage). The inventory
dashboard renders it as "Open Demand vs Stock on Hand"; `inventory:badge` counts
`InventoryService.attention_count()` = in-date stock expiring inside the alert window +
usable-status bags past their date + items in shortage.

One definition per number, everywhere: `expiring_window_days()` is the only reader of
`expiring_soon_days` (dashboard header, matrix column, bag-list filter, alert window), and
"available" always means `status=AVAILABLE and expires_at > now` on the inventory dashboard,
the staff/admin dashboards (`core/views.py`) and the badge. Date-stale bags are listed by
`InventoryService.usable_past_expiry()` and surfaced as a banner pointing at
`manage.py expire_blood_bags` rather than being silently dropped, since the expiration rule
is a scheduled command. Bag-list deep links use comma-separated `status` values (validated
against `BloodBag.Status`) plus `expiry=active|soon|expired`, so a tile always links to
exactly what it counted. `_bag_table.html` names the owning request for reserved/issued
bags via a `Prefetch(..., to_attr="open_allocations")`.

## Frontend conventions

- Single base layout (`templates/base.html`) with role-aware topbar; centered
  card layout (`base_auth.html`) for login/anonymous token pages.
- The public landing page (`templates/core/home.html`) is a **standalone**
  shell (no `base.html`): fixed WebGL canvas (`static/js/landing-scene.js`,
  Three.js ES module — drifting blood cells reacting to cursor/scroll) plus
  GSAP choreography (`static/js/landing-anim.js`, CDN GSAP + ScrollTrigger).
  The layer is strictly decorative: content renders fully without JS (all
  animations are `gsap.from`), counters start at their server-rendered
  values, `prefers-reduced-motion` renders one static frame, and any
  CDN/WebGL failure falls back to the CSS gradient backdrop.
- Shared components in `templates/components/`: `field.html`,
  `form_errors.html`, `pagination.html`, `status_badge.html`, `card.html`,
  `confirmation.html` (Alpine confirm dialog), `nav_badge.html` (sidebar
  count pill). **Caution:** `{# … #}` template
  comments must never contain `{`/`%` — Django's comment regex `[^{}]+?`
  leaks such tags as live nodes (this once compiled a self-include into
  `pagination.html`). A test enforces this (`core.tests`).
- Sidebar live counts: `{% nav_item %}` takes an optional `badge_url` that
  htmx polls (`load, every 60s`) into the link, rendering
  `components/nav_badge.html`. `requests:badge` counts what awaits the viewer
  (`BloodRequestService.awaiting_action_count` — staff: undecided plus
  approved requests still owing units; requester: own open requests),
  `inventory:badge` counts stock work for staff
  (`InventoryService.attention_count` — in-date bags expiring inside the
  configured window, usable-status bags already past their date, and approved
  items with a compatibility-checked shortage), `accounts:registration_badge`
  counts PENDING registrations and `requests:walk_in_badge` counts counter
  records the desk still owes bags for (`walk_in_action_count`, staff-side
  only). The pill is
  polled rather than context-processed because the sidebar is outside
  `#page-content`, so modal-success refreshes would leave a server-rendered
  count stale.
- Filter/search forms use `hx-get` + `hx-target="#results"` + `hx-push-url`
  with a plain-GET fallback; pagination preserves query strings via the
  `{% querystring %}` tag.
- All form styling applied server-side (`StyledModelForm`/`StyledFormMixin`),
  so non-HTMX rendering looks identical.

## Printable documents (print preview + PDF)

Every formal document — all ~20 reports and the Inventory Statement — is
produced from **one** template, `templates/documents/document.html`, driven by a
`core.documents.DocumentSpec`. That is the whole point: a printed page, a print
preview and a downloaded PDF are three renderings of one object, so they cannot
drift apart.

```
DocumentSpec  ──►  render_document(chrome=True)   → print preview page (HTML)
             └─►  render_document(chrome=False)  → xhtml2pdf  → .pdf download
```

- **One template, two audiences.** It is written in the intersection of
  browser-print CSS and the subset xhtml2pdf implements: table-based layout, no
  flexbox/grid, no CSS variables, explicit colours. Anything outside that subset
  would either be dropped by the PDF engine or shift the print layout, so it does
  not belong in this template. The on-screen "desk" chrome and the action
  toolbar live behind `{% if chrome %}` and are therefore **structurally**
  impossible to reach a printed page — not merely hidden with CSS.
- **Views are thin.** `core.mixins.PrintableDocumentMixin` gives a view a
  `document()` (preview) and a `pdf()` action from one
  `build_document_spec()` implementation. `reports/views.py` and
  `inventory/views.py` only describe *what* the document says; `core/documents.py`
  decides how it is typeset.
- **Role gates are not bypassable.** Each document view keeps the same
  `StaffRequiredMixin`/`AdminRequiredMixin` as the page it belongs to, and both
  actions re-run the report's own `roles` check before touching data — printing
  and exporting are not a way around the registry.
- **Filtering carries over, pagination does not.** Export links preserve the
  active filters but strip `?page=` (`reports.views._url_with_query`): a printed
  document is the whole result set, so inheriting a page cursor would print a
  misleading slice.
- **Automatic landscape.** Tables of eight or more columns print landscape
  (`LANDSCAPE_FROM_COLUMNS`), plus a per-report opt-in table (`is_landscape`).
  Paper size comes from `settings.REPORT_PAPER`.
- **Text safety.** reportlab's built-in fonts are WinAnsi-only, so `→`, `≤`, curly
  quotes and accented input would render as blank boxes. `pdf_safe_text()` folds
  them for the PDF only (the preview keeps the nicer glyphs); these are
  presentation characters, so nothing meaningful is lost.
- **Fail safe, never fail silently.** A missing or failing PDF backend raises
  `DocumentRenderError`, and `PrintableDocumentMixin.pdf()` turns that into a
  message plus a redirect to the print preview (which always works) — it never
  returns a file that is not a valid PDF.
- **Honest truncation.** The Inventory Statement caps its bag listing at
  `STATEMENT_LIST_LIMIT`; if a filter matches more, the document *says so* in its
  notes. Silently truncating a stock count would be the dangerous option.

Global `@media print` rules in `static/css/input.css` (built into
`static/css/tailwind.css`) make **any** page printable, not just the documents:
chrome and filter bars drop out, content expands to full width, `thead` repeats
on every page, rows never split, and `print-color-adjust: exact` keeps status
colours meaningful on paper.

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
- **Exception — heavy detail screens are pages, not modals.** A screen whose job
  is *working*, not *previewing*, stays a full page no matter what the other
  screens do. Today that is the **blood bag record** (`inventory:bag_detail`):
  it carries the record-test-result form, second-person verification and the
  guarded release/transition actions, so `BagDetailView` uses plain `render()`
  (not `render_any`) and its template extends `base.html` with no `{% if layout %}`
  branches. Bag links are plain anchors everywhere — bag list, movement ledger,
  inventory dashboard, compatibility check, request detail, donation detail and
  the staff dashboard.
  Why it is also the *guard*: a `{% if layout %}` fragment would half-render
  silently if some link regressed. Rendering the full document unconditionally
  means a reintroduced `hx-get` injects a whole `<html>` into `#modal-root` and
  breaks loudly. `inventory.tests.BagDetailIsAPageTests` pins this, including a
  scan that fails if any template binds a `bag_detail` link to `hx-get` again.
  The test to copy: if a screen has **forms plus more than one action**, it is a
  page. Reserve modals for short read-and-dismiss views and small forms.
