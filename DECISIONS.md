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

## Open decisions (need the operator)
- Approval/replacement of every seeded clinical placeholder (D-003 follow-up).
- Real SMS provider + credentials.
- Label/QR hardware & format (constraint: encode only public `BB-` codes).
- Appointment day capacity rules (model exposes day_capacity_used; not yet
  enforced against a configured limit — REQUIRES CLARIFICATION).
- Directed/replacement donation policy specifics (enum exists; workflow
  rules unconfigured).
