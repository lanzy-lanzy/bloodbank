# Requirements Specification (specs.md)

Blood Bank Management System — Tambulig. Derived from the project brief
("PROMPT 0" requirements analysis). Status legend: ✅ implemented + tested,
🟡 implemented with configuration pending, ⛔ not implemented (see
API_ROADMAP / open-questions).

## 1. Purpose & scope

Web-based system to manage a blood bank's complete operational lifecycle:
donor registration → screening → donation → collection → testing / safety
screening → blood bag inventory → requests → compatibility → release →
emergency donor notification → rewards → reports → audit logs. Not a
laboratory information system; not a medical decision engine — it records
institution-verified outcomes and enforces configured rules.

## 2. Actors & permissions

| Actor | Capabilities |
|---|---|
| ADMIN | All operational + user management + system/blood-bank configuration (blood types, components, test types, compatibility rules, eligibility thresholds, reward values, templates) + audit viewing |
| STAFF | Donor registry & screening, donation & collection workflow, testing entry/verification, inventory transitions, request review/approval, allocation & issue, notifications & retries, reward adjustments, reports (except admin-only) |
| DONOR | Own profile (contact fields only), self-book appointment (requested status only), own donation history, own rewards & redemption, own notifications & emergency-response replies |
| REQUESTER | Create/manage blood requests for own organization only, view status & provide fulfillment feedback (transfused/returned) |

✅ RBAC via role mixins + per-view checks; ✅ negative-access tests + HTTP
walk verification.

## 3. Functional requirements

### 3.1 Donor management
- Unique donor code ✅; contact demographics ✅; optional account link ✅.
- Status lifecycle (active/temp-deferred/permanent-deferred/inactive/
  blacklisted) ✅; deferral reason + until date ✅.
- Soft deletion preserving history ✅.
- Eligibility evaluation from **configured** rules only ✅; fail-safe
  `REQUIRES_STAFF_REVIEW` when unconfigured 🟡 (institution must supply
  values — demo values are labelled placeholders).

### 3.2 Screening
- Configurable question set ✅ (model + admin UI for questions).
- Recording: identity verification, consent, vitals, hemoglobin, outcomes
  (cleared / deferred n-days / rejected / needs review) ✅.
- Latest screening feeds eligibility ✅.

### 3.3 Donation & collection
- Lifecycle state machine with reasons on negative outcomes ✅.
- Collection creates bag(s) atomically, starts QUARANTINED ✅; linked
  appointment auto-completed ✅; configured reward rule awarded ✅ (no-op
  when unconfigured).

### 3.4 Testing / safety screening
- Test types configured with category + REQUIRED flag ✅.
- Results with raw value + status + performer; **separate verification**
  by another record's actor (verified_by) ✅.
- Release guard: every active required test latest = NON_REACTIVE and
  verified; zero required-tests configured ⇒ release blocked ✅ (tested).

### 3.5 Inventory
- Bag master data incl. component, volume, storage location/position ✅.
- Statuses: QUARANTINED, TESTING, AVAILABLE, RESERVED, ISSUED, RETURNED,
  EXPIRED, REJECTED, DISCARDED, TRANSFUSED ✅ with locking transitions ✅,
  full movement ledger ✅, manual external-bag registration (audited,
  reason mandatory) ✅, expiry job ✅ + expiring-soon window from config ✅.
- Bag codes never expose donor info ✅ (label printing/QR ⛔ roadmap).

### 3.6 Requests
- Organizations + authorized requester accounts ✅.
- Request with items (type/component/quantity), urgency levels,
  required-by time, optional supporting document (validated extension) ✅.
- Lifecycle DRAFT→SUBMITTED→REVIEW→APPROVED→FULFILLED with reject/cancel/
  expire ✅.

### 3.7 Compatibility & allocation
- Compatibility from approved rule rows only; absence ⇒ incompatible ✅
  (tested four rule-shape cases).
- Allocate AVAILABLE compatible unexpired bag → RESERVED with per-bag
  unique active allocation ✅; issue with confirmation token ✅;
  transfuse/return feedback from requester ✅; cancel restores stock ✅.

### 3.8 Emergency donor notification
- Candidate pool from configured compatibility + ACTIVE donors ✅ (pool is
  a shortlist, not a medical clearance — labelled in UI).
- Multi-channel send (in-app/email real-ish, SMS **MOCK** 🟡);
  delivery statuses + retry ✅.
- Anonymous capability-token response links (UUID), first response kept,
  disclaimer ✅ (tested).

### 3.9 Rewards
- Configured point rules per event, milestone rules ✅; ledger with running
  balance and idempotent event awards ✅; tiers by configured thresholds ✅;
  redemption debits + stock control ✅; balance reconciliation command ✅.

### 3.10 Reports
- ~20 role-gated reports across donors/donations/inventory/requests/rewards/
  audit with filters + pagination ✅; CSV export with generation timestamp ✅;
  dashboard charts from JSON endpoints ✅.

### 3.11 Audit
- Append-only log of every significant action with actor/IP/before/after ✅;
  three-level immutability enforcement ✅; admin report + viewer ✅.

## 4. Non-functional requirements

| Req | Status |
|---|---|
| Server-side validation always | ✅ forms + services; client attrs only cosmetic |
| Transactions + row locking on inventory/reward writes | ✅ select_for_update in services (PostgreSQL enforces truly; SQLite serializes writes at DB level) |
| Immutable audit | ✅ model+queryset level, tested |
| Soft deletion for history | ✅ donors; ledgers never edited |
| Secrets via env only | ✅ settings + `.env` (git-ignored); demo keys clearly named insecure defaults |
| SQLite dev / PostgreSQL prod | ✅ dj-database_url |
| No destructive migrations/data loss | ✅ additive-only; seed command is idempotent and never deletes |
| Tests per module | ✅ 106 tests + E2E walkers |
| No placeholder claimed as complete | ✅ MOCK providers labelled; unconfigured states surfaced in UI |
| Configurable business rules — nothing clinical hard-coded | ✅ rule tables + settings; compatibility/eligibility/rewards/tests all DB-driven |
| Donor privacy | ✅ donors see only own data (tested) |
| Requester scoping | ✅ own organization only (tested) |
| Staff cannot change critical config | ✅ AdminRequiredMixin (tested) |
| QR/barcode without confidential info | 🟡 not implemented; constraint documented (bag_code only) |
| E2E verification per role | ✅ GET walk (104 pages × 4 roles) + POST workflow smoke |

## 5. Explicit open questions

See [docs/open-questions.md](docs/open-questions.md) — institutional
clinical values (approval of seeded matrix), SMS provider, HIS/LIS
integration, label printing hardware, appointment capacity rules,
directed-donation policy. None block the current build; all are surfaced
in-product as REQUIRES CONFIGURATION / REQUIRES CLARIFICATION.
