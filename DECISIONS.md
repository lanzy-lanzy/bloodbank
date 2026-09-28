# Decision log (DECISIONS.md)

Short ADR-style entries for decisions taken while building the system.
Context assumes the project brief (Django + HTMX, four roles, fail-safe
clinical posture, no invented medical rules).

## D-001 — Server-rendered Django templates + HTMX, no SPA
Chosen in the brief. Kept: HTMX fragments for filters/pagination, Alpine for
small client state (dialogs, toggles), Chart.js CDN for dashboard charts.
Consequence: no node build pipeline; templates are the UI layer of record.

## D-002 — Custom user model with a `role` field instead of groups
Four fixed roles → a `CharField(choices)` on `accounts.User` + mixins
(`StaffRequiredMixin`, `AdminRequiredMixin`, role checks in views). Simple,
testable, and role is visible on every auth surface. Superuser is NOT needed
for ADMIN capabilities (tested).

## D-003 — Configuration-driven clinical rules, seeded UNAPPROVED
All clinically significant values live in DB tables. The demo seed loads the
standard ABO/Rh red-cell matrix, eligibility thresholds, shelf lives and
reward values only as clearly-labelled DEMO placeholders; compatibility rows
are seeded with `approved_by = NULL` and rendered "NOT APPROVED". Rationale:
anti-hallucination rule — the app never applies clinical values a human
institution hasn't configured/approved.

## D-004 — Absence-of-rule = incompatible / release-blocked
Compatibility with no matching rule is FALSE; zero required tests configured
blocks ALL release; missing eligibility thresholds return
`REQUIRES_STAFF_REVIEW`. Fail-closed everywhere, because fail-open would be
a patient-safety issue. (Tests: `inventory.tests.CompatibilityEngineTests`,
`ReleaseGuardTests`, `donors.tests.EligibilityRuleTests`.)

## D-005 — Service-layer state machines with DB row locking
`TRANSITIONS` dicts on models (source of truth) + services that
`select_for_update()` inside `transaction.atomic()`, validate the move,
write ledger + audit. Views and confirmation tokens are UX layers; the
service re-checks every invariant. SQLite dev serializes writes anyway;
PostgreSQL prod gets true row locks.

## D-006 — Ledgers are append-only, corrections are new entries
`InventoryTransaction` and `PointTransaction` never updated; balance/status
corrections are ADJUSTMENT entries. `Donor.points_balance` is a cache
reconcilable from the ledger (`recalculate_reward_balances`).

## D-007 — Audit immutability enforced at three levels (found via tests)
Model `save()` blocks row updates; model `delete()` blocks instance deletes;
AND `ImmutableAuditQuerySet.delete()/update()` block bulk paths — queryset
deletes bypass per-instance `delete()` in Django. The third layer was added
after the test suite proved the bypass. Docs updated (SECURITY.md).

## D-008 — Soft deletion for donors only; everything else terminal-states
Donors can be "removed" without losing donations/bags/ledger/audit links.
Bags/transactions/requests close via terminal states (DISCARDED, TRANSFUSED,
CANCELLED), never deletion.

## D-009 — Capability-UUID emergency response links
Anonymous donors get `/notifications/respond/<uuid-token>/` — unguessable
capability, scoped to exactly one notification's four availability answers,
first-write-wins, always displaying "availability only… not a medical
clearance". Chosen over requiring donor logins during emergencies. Tests
cover idempotency, invalid values, and pk-path ownership checks.

## D-010 — Provider interfaces with labelled mocks
`NotificationProvider` interface; InApp real, email via Django mail (console
in dev), SMS `MockSMSProvider` — MOCK — DEVELOPMENT ONLY. `retry_count`
semantics = number of retry attempts (increments on each retry, success or
not) — settled during testing when a successful retry left the counter 0.

## D-011 — Login-URL middleware bug class fixed properly
`LOGIN_URL` is a URL name ("accounts:login"); middleware comparing it against
`request.path` broke the login page itself (redirect loop) and blocked
anonymous password-reset confirm links. Decision: `resolve_url()` at
middleware import + regression tests (`core.tests.PublicPathsTests`) +
password-reset paths added to the public allowlist.

## D-012 — Template component comment policy
`{# #}` comments must be single-line and free of `{`/`%`: Django's comment
lexer rejects them, so brace-bearing comments compile as live nodes (real
RecursionError incident — self-included `pagination.html`) and multi-line
comments render as VISIBLE TEXT on every page that includes the component
(found by the live browser spot-check on the login form). Both cases are now
scanned for by `core.tests.TemplateComponentTests`. Compile-check passing
(`get_template`) does NOT prove render-safety for this bug class.

## D-013 — Dev helpers stay at repo root, documented
`check_templates.py`, `check_e2e.py`, `check_e2e_post.py` kept as
documented verification tools (TESTING.md) rather than folded into an
installable test package: they target the seeded dev DB and a live server
walk; `manage.py test` remains the CI-grade source of truth. POST smoke is
rollback-safe by design.

## D-014 — SQLite dev / PostgreSQL prod via DATABASE_URL
`dj_database_url` with SQLite default; production docs mandate PostgreSQL
(reason: `select_for_update` semantics under concurrency). Seed/demo data is
explicitly non-production.

## D-015 — No public API yet; roadmap instead of half-built endpoints
Rather than expose thin endpoints, API_ROADMAP.md defines phased,
service-bound designs (read-only first; LIS ingestion must never be able to
self-verify results).

## D-016 — Semaphore is the real SMS provider (opt-in, env-only creds)
`SemaphoreSMSProvider` (API v4, `POST https://semaphore.co/api/v4/messages`,
form fields `apikey`/`number`/`message`/`sendername`) implements the same
`NotificationProvider.send() -> (ok, error)` contract, so `NotificationService`
dispatch/retry/audit paths are unchanged. Selected only by
`SMS_PROVIDER=semaphore`; default stays `mock`. Missing `SMS_API_KEY` fails
safe — no HTTP call, FAILED + reason on the row (hard rule 6 posture kept:
never fake delivery). Recipient numbers are normalized to Philippine local
form `09XXXXXXXXX` (`+63`/`63` prefixes accepted); an unnormalizable number
fails rather than being guessed. Credentials come from env only. Live delivery
is NOT claimed operational until real credentials are configured and verified.

## D-017 — Reservation quota: in-flight `RESERVED` allocations count against the item
`RequestItem` had only `quantity` / `fulfilled_quantity`, so the allocate guard
checked `outstanding` (issued blood only) and two staff could each reserve stock
for the same requested unit — proven on the dev DB (a 1-unit FULFILLED request
carrying 2 active allocations). The item now exposes three counters —
`outstanding`, `units_reserved`, `reservable_units` — the guard refuses at
`reservable_units == 0` and re-reads the item under `select_for_update()` inside
its transaction. Demand-style screens (the inventory dashboard's open-demand
panel, shortage counts, the allocate dropdown messaging) measure against
`reservable_units` too, because a reserved bag has already left `AVAILABLE` and
must not read as a hard shortage; `fulfilled_quantity` still counts issued blood
only, so fulfillment accounting is unchanged. `reconcile_inventory` gained
`[OVER-RESERVATION]` and `[ORPHAN]` checks as the read-only net.

## D-018 — Walk-in requests keep an owning organization (configured desk), not a nullable FK
A patient can request blood at the counter with no requester account. Making
`BloodRequest.organization` nullable was rejected: ~15 call sites
(reports/CSV, dashboards, notifications, audit descriptions, the open-demand
panel) read `organization.name`, and organization membership is what the
requester-scoping checks compare against — a null would be both a crash source
and an authorization question mark. Instead `channel=WALK_IN` marks the record
and it is booked against the organization named by the new
`walk_in_organization_id` setting (seed_demo provides "Walk-in Desk (Blood
Bank)"; institutions repoint it). Unconfigured ⇒ intake is refused with
REQUIRES CONFIGURATION rather than an arbitrary organization being guessed
(hard rule 1 posture). Consequences held by tests: the desk is not selectable
on the organization channel, walk-in rows never appear for any requester
(list, detail 403, sidebar count, requester dashboard) even if they belong to
that organization, and staff — not a requester — record transfusion/return on
walk-in allocations.

## D-019 — Walk-in requests are validated at the counter, not approved
The approval queue exists because a hospital's requester account is not trusted
to self-authorize blood. A walk-in has no such party: a staff member at the
counter took the request in person, so an Approve/Reject step would be staff
approving their own form — and it is what the user asked to drop ("walk in is
direct request on clinic"). Chosen: `validate_walk_in()` stores the row
APPROVED at creation with `approved_by`/`approved_at` = that staff member, under
its own audit action `REQUEST_VALIDATED_AT_COUNTER`.

Rejected alternative: let walk-ins allocate straight from SUBMITTED. Every guard
that matters keys on APPROVED status (`allocate_bag`, `open_demand`,
`awaiting_action_count`, the shortage panel, reports), so a second "eligible for
allocation" state would fork each of them for no operational gain — and would
erase who authorized the demand.

Consequences held by tests: `approve()` and `reject()` raise on a walk-in row
(UI hides the buttons; the service refuses a forged POST), `submit()` routes a
walk-in draft to validation so `SUBMITTED` never occurs on that channel, no
requester notification is sent (there is no requester account, and the desk owns
no logins), and an unservable walk-in is closed with **cancel** like any other
request. Traceability is unchanged: the counter staff member is recorded, so the
release chain still reads staff → issue (token-confirmed) → transfuse/return.

## Open decisions (need the operator)
- Approval/replacement of every seeded clinical placeholder (D-003 follow-up).
- Semaphore API key + registered sender name; live-send verification with the
  provider once credentials exist (D-016).
- Label/QR hardware & format (constraint: encode only public `BB-` codes).
- Appointment day capacity rules (model exposes day_capacity_used; not yet
  enforced against a configured limit — REQUIRES CLARIFICATION).
- Directed/replacement donation policy specifics (enum exists; workflow
  rules unconfigured).
