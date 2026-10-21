# Database

SQLite in development (`db.sqlite3`, created by `manage.py migrate`);
PostgreSQL in production via `DATABASE_URL`. 31 models across 11 apps;
33 migrations applied. Migrations are additive — nothing in this codebase
drops or truncates tables, and destructive changes must be reviewed manually.

## Entity overview

```
accounts.User (AUTH_USER_MODEL) ── role: ADMIN | STAFF | DONOR | REQUESTER
                     │ 1:1                    │ 1:1
             donors.Donor             requests.RequesterProfile ──→ requests.Organization
                                          │ 1:N
                                        requests.BloodRequest ──1:N→ requests.RequestItem
                                                              ──1:N→ requests.Allocation ──→ inventory.BloodBag
accounts.RegistrationRequest ──1:1→ accounts.User (inactive until APPROVED)
accounts.RegistrationRequest ──1:1→ accounts.RegistrationInterview (donor only, staff-review record)

donors.Donor ──1:N→ donations.Donation ──1:1→ appointments.Appointment
           │        Donation.blood_type → inventory.BloodType
           ├──1:N→ donors.DonorScreening (→ ScreeningQuestion / ScreeningResponse)
           ├──1:1→ donors.DonorInterviewSheet ──1:N→ donors.InterviewAnswer (Q01…Q37)
           ├──1:N→ inventory.BloodBag (from collection; donation FK null = external bag)
           └──1:N→ rewards.PointTransaction / DonorReward

inventory.BloodBag ──1:N→ inventory.TestResult (→ TestType)
                 └──1:N→ inventory.InventoryTransaction (append-only ledger)

settings_app.SystemSetting · inventory.BloodType/BloodComponent/TestType/CompatibilityRule
· rewards.RewardRule/RewardTier/Reward · notifications.NotificationTemplate/Notification
· audit.AuditLog (append-only)
```

## Key fields and constraints

### accounts.RegistrationRequest
Public self-registration awaiting admin review. Identity fields
(`first/middle/last_name`, `username`, `email`, `phone`, `role` limited to
DONOR / REQUESTER), donor extras (`sex` — FEMALE / MALE / OTHER /
UNDISCLOSED, `blood_type` FK null, `date_of_birth`, address fields) and
`organization_name` for requesters. `sex` is required for donors at submit,
decides whether the sheet's female-donor block applies and seeds
`Donor.sex` at approval; a requester row always stores UNDISCLOSED. 1:1
`user` FK to the
**inactive** `accounts.User` created at submit time (password hashed then;
never stored in this row). `status` state machine PENDING → APPROVED |
REJECTED is one-shot (`TRANSITIONS` dict + `RegistrationService.review`
guards, incl. self-review block and mandatory rejection reason);
`reviewed_by` / `reviewed_at` / `rejection_reason` are append-only review
columns. Approving flips `user.is_active`; rejecting leaves the account
blocked. Duplicate username/email are rejected case-insensitively at form
level; `phone` must be a valid Philippine mobile (SMS reachability).

### accounts.RegistrationInterview
1:1 to `RegistrationRequest` (`registration`, `related_name="interview"`,
CASCADE). The **donor interview sheet** self-declared on the public
registration form when role=DONOR (REQUESTER registrations have none). It *is*
the printed questionnaire: one nullable boolean per YES/NO item — `q01` …
`q33`, `q35`, `q36` — named after the sheet key (`Q07` -> `q07`) and labelled
with the sheet's own question text. The field list is derived from
`donors/interview.SIGNUP_FIELDS` and exposed as `QUESTION_FIELDS`, so the form
and the sheet can never disagree about which item an answer belongs to. The
two DATE items (Q34 last childbirth, Q37 last menstrual period) have no tick
box and are not asked at sign-up; the female-donor block (`q33`, `q35`,
`q36`) is asked only when the applicant declared FEMALE — everyone else stores
NULL there, because blank is **not** a No. Plus `declaration` (applicant
certification, required at submit) and free-text `notes`. The eleven boolean
columns of the previous form (`felt_well_today`, `on_medication`, …,
`previously_deferred`) are kept for history but never asked, rendered or
required: migration `accounts.0005` copied only the exact-match answers into
their `q..` field. `answered_questions()` feeds the review page.

It is a **record for admin review only**: the system computes no eligibility,
deferral, or auto-approve/auto-reject from these answers (clinical rules live
with staff and `donors.*` screening — see agents.md rule 1). Written by
`RegistrationService.submit` alongside the registration; surfaced on the
registration review page. At approval,
`InterviewSheetService.ensure_for_donor` copies each answer onto the
`InterviewAnswer` of the same item key, so every item the applicant answered
prints ticked.

### donors.Donor
`donor_code` (auto `DON-000001`), optional 1:1 `user` link, `blood_type` FK,
`status` (ACTIVE / TEMP_DEFERRED / PERM_DEFERRED / INACTIVE / BLACKLISTED),
`deferral_reason`, `deferred_until`, `points_balance` (cached; ledger is
authoritative), `is_deleted` + `deleted_at` (soft deletion; default manager
hides deleted rows, `all_objects` keeps history intact).

### donors.DonorInterviewSheet / donors.InterviewAnswer
1:1 to `donors.Donor` (`related_name="interview_sheet"`) plus a nullable 1:1
to the `accounts.RegistrationRequest` that produced the donor. Header fields
mirror the printed PRC Blood Donor Interview Sheet (name parts, birth date,
age on interview, sex, civil status, blood type, the four address boxes in
free text, home/work numbers, office use, donor/interviewer IDs). The CUE
(Confidential Unit Exclusion) block is `cue_choice` (SAFE / NOT_SAFE; blank
means **not recorded**, never an assumed answer), `cue_serial_number`,
`cue_date_of_donation`, `cue_place_of_donation`. Signatures are
`donor_signature` / `donor_signed_on` and `interviewer_signature` /
`interviewer_signed_on`. `status` DRAFT → COMPLETED is one-shot with
`completed_at` / `completed_by`; saving a signed sheet with completion turned
off reopens it and is audited as `INTERVIEW_SHEET_REOPENED` (further signed
saves are `INTERVIEW_SHEET_AMENDED`). Answers live in `InterviewAnswer`:
`sheet`, stable `item_key` (`Q01`…`Q37`, `donors/interview.py`),
`answer` (`YES` / `NO` / empty — empty is **not** a No) and `remarks`,
unique per sheet+item. `InterviewSheetService.ensure_for_donor` generates the
sheet (all 37 rows, blank) and pre-fills only the answers the registration
self-declaration actually answers: a statement worded as the item transfers
Yes and No, a broader statement transfers only a Yes. It is idempotent and runs on registration
approval, counter registration and `seed_demo`. Saving records the interview;
it never changes donor status or eligibility.

### donations.Donation
`donation_code` auto, `donor`, optional 1:1 `appointment`, date/time,
`blood_type` (copied from donor at creation), `status` state machine,
`volume_ml`, `donation_type` (VOLUNTARY/REPLACEMENT/DIRECTED).

### inventory.BloodBag
`bag_code` auto `BB-YYYY-NNNNNN` (this is the only value safe for labels /
QR codes — it exposes no donor information), `donation` (null only for
audited external registrations), `donor`, `blood_type`, `component`,
`collected_at`, `expires_at` (derived from component shelf life at
collection; per-component override possible), `status` state machine,
`released_by/released_at`, `screening_status`.
Constraint surface: state machine enforced in `InventoryService.transition`
with `select_for_update`; expiration date re-checked on every transition
into usable stock.

### inventory.TestResult
`bag`, `test_type`, `result` (raw value text), `result_status`
(PENDING / NON_REACTIVE / REACTIVE / INDETERMINATE…), `performed_by`,
`verified_by`, `verified_at`. Release guard consumes the LATEST result per
required test type.

### inventory.CompatibilityRule
`patient_type`, `donor_type`, optional `component` (null = all components),
`is_allowed`, `approved_by`, `approved_at`, `is_active`, `note`.
Absence of a rule is treated as NOT compatible (fail-safe). Demo seed loads
the standard ABO/Rh red-cell matrix UNAPPROVED for institutional review.

### requests.BloodRequest / RequestItem / Allocation
Request has `request_code` auto, organization, `channel`
(ORGANIZATION = submitted by a requester account / WALK_IN = logged by staff
for a patient at the counter, with `walk_in_contact` + optional
`walk_in_phone`), urgency
(ROUTINE/URGENT/EMERGENCY/CRITICAL), `patient_reference` (minimum necessary
identifier — no clinical free text), status machine, approval columns.
Walk-in rows are booked against the organization named by the
`walk_in_organization_id` setting and are excluded from every requester-facing
query. A walk-in skips the approval queue: `validate_walk_in()` stores it as
APPROVED with `approved_by`/`approved_at` set to the counter staff member,
auditing `REQUEST_VALIDATED_AT_COUNTER`, so `SUBMITTED`/`UNDER_REVIEW` never
occur on `channel=WALK_IN` rows (enforced in `approve()`, `reject()` and
`submit()`).
Item: blood_type + component + `quantity` / `fulfilled_quantity`, plus the
derived accounting trio `outstanding` (quantity − fulfilled),
`units_reserved` (RESERVED allocations on the item) and `reservable_units`
(outstanding − units_reserved) — reservation quota is enforced against
`reservable_units` under `select_for_update()`. Allocation: request + item + bag, status
(RESERVED/ISSUED/RETURNED/CANCELLED, `ACTIVE_STATUSES` = RESERVED|ISSUED), **unique partial constraint: one
active (RESERVED|ISSUED) allocation per bag**.

### rewards.PointTransaction
Signed `amount`, `type` (EARN/REDEEM/BONUS/ADJUSTMENT), `running_balance`
per donor, optional `rule`/`reward` FKs, `reference` (idempotency key for
event awards). `Donor.points_balance` is reconciled from the ledger
(`recalculate_reward_balances` command exists for auditing).

### notifications.Notification
Recipient is `donor` OR `user`; `channel` (in_app/email/sms); delivery
status (PENDING/SENT/FAILED) + `error` + `retry_count`; `response_token`
unique UUID (public capability for donor responses); `donor_response`
first-write-wins; read tracking.

### settings_app.SystemSetting
Unique `key`, string `value` + `value_type` (str/int/bool) → `typed_value()`,
`category`, `is_sensitive` (masked in UI). Eligibility thresholds
(`min_age_years`, `max_age_years`, `min_donation_interval_days`,
`min_weight_kg`, `max_donations_per_year`), inventory thresholds
(`low_stock_threshold`, `expiring_soon_days`) live here — NOT in code.
`walk_in_organization_id` names the organization that owns counter (walk-in)
requests; unset or pointing at an inactive organization disables walk-in intake.

### audit.AuditLog
Append-only: `user`, `action`, `module`, object reference, `ip_address`,
JSON `before_state`/`after_state`, `description`, `created_at`.
Immutability enforced at THREE levels: model `save()` (existing pk), model
`delete()`, and `ImmutableAuditQuerySet.delete()/update()`.

## Historical-record policy

- Donor deletion is SOFT (`is_deleted`) — donations, bags, ledger and audit
  rows survive and remain traceable.
- Blood bags, transactions, test results, allocations and audit logs are
  never hard-deleted by the application; state machines + terminal states
  (DISCARDED / TRANSFUSED / CANCELLED) close records out.
- `InventoryTransaction` and `PointTransaction` are ledgers: corrections are
  new ADJUSTMENT entries, never edits of old rows.

## Indexes and integrity highlights

DB-level: unique codes (`donor_code`, `bag_code`, `request_code`,
`SystemSetting.key`, `NotificationTemplate.code`, `RewardRule.code`,
`Notification.response_token`), partial unique constraints (open appointment
slot per donor; one active allocation per bag), FK `on_delete=PROTECT` on
clinical/inventory relations, `db_index` on all status columns.
