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

## D-020 — One case for the record and the paper (upper-case text fields)
The interview sheet is printed from stored data, so two casings would mean two
answers to "what does the record say". Chosen: identity text is upper-cased once
on the way in (`core/text.uppercase_fields` — registration submit, donor linking,
sheet save, including the sheet's own text fields) and again at render time
(`donors/documents.py`), so a legacy mixed-case row still prints the way the
clinic files it. `username` and passwords are deliberately excluded: a login
credential is not display data, and changing its case changes how the applicant
signs in. `interview_notes` stays as typed (narrative, not an identity field).

The PENDING preview is a read, not a draft record: it builds a transient donor
(`PREVIEW-00001` code) and sheet in memory and writes nothing, so "verify before
approving" cannot create a donor that approval did not. It is staff-visible (the
user's request) while the approve/reject screen stays admin-only, and it returns
404 once the application leaves PENDING.

## D-021 — The pre-submission preview is the real document, rendered from an unsaved form
An applicant should see exactly what the clinic will store before committing,
not a lookalike summary. Chosen: the review step offers a public **POST**
(`public_registration:register_preview`) that validates the same
`RegistrationForm`, builds the transient donor/sheet in memory
(`InterviewSheetService.preview_for_application`) and returns the *same*
`form_document.html` staff and the PDF use, with print-only chrome
(`X-Register-Preview: sheet`). Nothing is written, so previewing cannot create
an account, registration, donor, sheet or audit row.

Rejected alternatives: (a) a second HTML mock of the sheet — two layouts drift,
and the applicant could "verify" fields the clinic never prints; (b) saving a
DRAFT application per preview — a preview would become a record the applicant
did not submit; (c) rendering the document into the page directly — its
PDF-safe stylesheet would leak into the sign-up page, so the stepper writes the
response into an `<iframe srcdoc>` instead. The button remains a real submit
with `formaction`/`formtarget`, so without JavaScript the browser posts to the
same URL in a new tab.

Coverage gap (as of D-021; **superseded by D-023**): 11 sign-up questions
could not answer the official 37 items, and inferring the rest would invent
clinical answers (agents.md rule 1). The preview therefore appended a separate
**Self-declaration recorded at sign-up** table (S1-S11) repeating every answer
verbatim, with a note that the remaining items are asked at the interview;
the signed sheet kept the official items only. D-023 replaced the eleven
statements with the sheet's own questionnaire, so that table was retired.
Rejected then and now: ticking official items from unrelated sign-up answers,
and copying every answer onto all 37 items.

Boundaries: a requester application has no interview sheet, and an invalid one
must not be previewed as complete — both get a short explanation page
(`X-Register-Preview: unavailable`), never a document with invented blanks. The
endpoint rejects GET (405), redirects signed-in users to their dashboard, and
is allow-listed exactly in `PUBLIC_PATHS_EXACT`.

## D-022 — The self-declaration is worded as the sheet items it pre-fills
*Superseded by D-023: the form now asks the sheet's own questionnaire, so there
are no mirrored statements left to word. Kept for the record.*

Operators expect the sheet to carry the answers they gave at sign-up, but the
original statements were narrower or broader than their items ("not currently
taking any medication" vs Q07 "in the past 4 weeks"; "no dental procedure in
72 hours" vs Q13 "in the past 12 months"). Copying those answers would print a
declaration the donor never made. Chosen: reword the sign-up statements to the
official item text (same window and scope) and treat them as mirrors, so Yes and
No both transfer. Q07 is answered by two statements (any Yes wins, No only when
both are No). Q33 stays Yes-only because the female block is not asked of an
undetermined sex at sign-up, and items with no statement stay blank for the
interviewer.

Consequences: the public form's wording changed (`accounts.0004`); the lifetime
transfusion statement became the 12-month Q12 item, and "shared needles" /
"breastfeeding" are no longer part of the sign-up statements (staff ask them at
the interview — Q14/Q21/Q36). Answers already stored keep their booleans under
the new wording — no data migration is attempted, so a PENDING row created
before this change is displayed with the reworded statement.

Rejected: ticking items from the old narrower statements (a clinical
misstatement) and leaving the sheet mostly blank (operators could not verify
their answers on the sheet).

## D-023 — The sign-up asks the printed questionnaire itself (one field per item)
The sheet prints 37 numbered lines with a YES/NO box each, but the sign-up form
asked eleven statements of which only eight could ever answer an official item
(D-022). A preview therefore showed two or three ticks plus a separate S1-S11
listing (D-021), and staff could not check most of the applicant's answers on
the sheet itself. Chosen: the public form asks the sheet's own YES/NO items.
`donors/interview.py` stays the only source of truth — `SIGNUP_FIELDS` is every
`YESNO` item (`Q07` -> `q07`), `RegistrationInterview` holds one nullable
boolean per item (`q01`…`q33`, `q35`, `q36`, exposed as `QUESTION_FIELDS`)
labelled with the sheet's own question text, and approval copies each answer
onto the `InterviewAnswer` of the same key. Every box the applicant answered
prints ticked and nothing is inferred: a blank stays blank.

Consequences: the form collects `sex` (FEMALE / MALE / OTHER / UNDISCLOSED),
because the female-donor block (Q33/Q35/Q36) applies only to female applicants.
It is required for donors, stored on `RegistrationRequest.sex`, seeds
`Donor.sex` at approval and decides whether the block is asked, validated and
pre-filled; for anyone else those three items are left NULL and are not shown
ticked on the sheet. The two DATE items (Q34 last childbirth, Q37 last
menstrual period) have no tick column and are not asked at sign-up. The eleven
legacy boolean columns are kept (agents.md rule 4) and migration
`accounts.0005` copies only the exact-match answers into their `q..` field
(q01, q02, q07, q12, q13, q14, q15, q23); nothing renders them any more. The
S1-S11 preview table is retired: the official grid now carries every answer on
the same document the applicant previews, staff review and the clinic prints.

Rejected: keeping a statement-to-item mapping table (two lists drift, and a
later wording change silently mis-ticks), inferring answers from broader
statements, and asking the two DATE items as free text at sign-up (the clinic
takes those at the interview; a typed date would print unverified). Boundaries
held: requesters answer no questionnaire, the server still requires every
non-female-block item for donors, and a preview still writes nothing.

## Open decisions (need the operator)
- Approval/replacement of every seeded clinical placeholder (D-003 follow-up).
- Semaphore API key + registered sender name; live-send verification with the
  provider once credentials exist (D-016).
- Label/QR hardware & format (constraint: encode only public `BB-` codes).
- Appointment day capacity rules (model exposes day_capacity_used; not yet
  enforced against a configured limit — REQUIRES CLARIFICATION).
- Directed/replacement donation policy specifics (enum exists; workflow
  rules unconfigured).
