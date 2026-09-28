# Open questions & configuration requirements

Anything the system cannot answer on its own, surfaced honestly. Items are
tagged **REQUIRES CLARIFICATION** (a human/institutional decision is needed)
or **REQUIRES CONFIGURATION** (a value/rule must be entered by an authorized
administrator; the app already fails safe until then).

## Clinical / regulatory

1. **Compatibility matrix approval — REQUIRES CLARIFICATION + CONFIGURATION.**
   The seeded ABO/Rh red-cell matrix is a textbook placeholder stored
   UNAPPROVED (`approved_by = NULL`). The operating blood bank's medical
   director must review, adjust (e.g. extended phenotyping, irradiation,
   CMV/PLP policies) and approve each rule in Settings → Blood Bank
   Configuration before production use.
2. **Eligibility thresholds — REQUIRES CONFIGURATION.** Min/max age,
   donation interval, min weight, max donations/year, inter-component
   intervals (platelets vs plasma vs RBC) — demo values only; some donor
   questionnaires defer decisions to staff until values are approved.
   Missing values ⇒ `REQUIRES_STAFF_REVIEW` (never a guess).
3. **Deferral rules from screening answers — REQUIRES CLARIFICATION.**
   `ScreeningQuestion` supports recording outcomes, but per-answer deferral
   day counts are an institutional policy not yet modelled as structured
   data (screening result carries free `deferral_days` per record).
4. **Required test panel per component — REQUIRES CONFIGURATION.** Which
   tests are mandatory (HBsAg, HCV, HIV, syphilis, malaria, grouping/rh…)
   and which are `is_required` for release is seeded as demo rows. The
   national regulatory panel must replace them.
5. **Shelf lives & storage — REQUIRES CONFIGURATION (per component).**
   Demo defaults (e.g. WB 21–35 d style placeholders) must be replaced with
   validated values; per-bag overrides are supported by the model.
6. **Plasma/cryoprecipitate & apheresis multi-component collection —
   REQUIRES CLARIFICATION.** Today one collection = one bag; component
   separation from one whole-blood donation is not modelled. Decision needed
   on whether to add split-component lineage (parent bag FK) vs external
   registration path.

## Operational

7. **Appointment capacity per session/day — REQUIRES CONFIGURATION.**
   `day_capacity_used` is computed and shown; no configured maximum is
   enforced at booking yet. Need the institution's drive/day limits.
8. **Emergency notification scope & cadence — REQUIRES CLARIFICATION.**
   Who is eligible to be auto-pooled (ACTIVE + compatible + contactable is
   current logic), how many donors per urgency level, retry cadence, and
   quiet hours are policy choices; provider selection for SMS depends on 9.
9. **Real SMS/email provider — REQUIRES CLARIFICATION + credentials.**
   SMS now has a live `SemaphoreSMSProvider` (Semaphore API v4) — inactive
   until `SMS_PROVIDER=semaphore` + `SMS_API_KEY` are set; default remains
   the labelled MOCK. Email defaults to console. Choose an SMTP relay and
   verify a live Semaphore send with operator-supplied credentials + test
   number before claiming operational delivery.
10. **Reward program values — REQUIRES CONFIGURATION.** Point values,
    milestone intervals, tier thresholds and redemption catalogue are demo
    data pending institutional approval; rules gate everything (unconfigured
    ⇒ no award, tested).
11. **QR/barcode label printing — REQUIRES CLARIFICATION.** Not
    implemented; needs printer/label format decision. Hard constraint
    already recorded: labels encode the public bag code (`BB-…`) only — no
    donor identifiers (SECURITY.md, API_ROADMAP.md).
12. **Requester authorization workflow — REQUIRES CLARIFICATION.**
    `RequesterProfile.is_authorized` exists and admin can manage accounts,
    but the formal process (documented license verification, who signs off,
    re-certification period) is an institutional SOP to encode.
13. **Walk-in counter policy — partly DECIDED, rest REQUIRES CLARIFICATION.**
    Walk-in intake is implemented (staff log the request against the configured
    `walk_in_organization_id` desk organization), and the operator has decided
    the workflow question: a walk-in is a **direct clinic request with no
    approve/reject step** — the counter staff member is the authority, so the
    record is validated at the counter (D-019) and closed with cancel if it
    cannot proceed. Still to be decided by the institution: what
    identification/proof is required at the counter, whether a physician's
    requisition must be attached (the document field exists but is optional),
    whether blood may be released to an individual at all (vs. only to a
    facility), and who may record transfusion/return afterwards. None of those
    are enforced in code today — the record captures a contact name, and release
    still requires the `ISSUE` confirmation token.

## Integration / infrastructure

14. **HIS/LIS integration — REQUIRES CLARIFICATION.** See API_ROADMAP
    Phase 2/3; needs partner system identities and transport choice
    (HL7 v2 / FHIR / CSV drop).
15. **Backup/retention policy — REQUIRES CLARIFICATION.** Audit trail is
    append-only forever today; a retention/archive policy (legal requirement
    period, cold storage) should be agreed with the institution.
16. **Environment specifics — REQUIRES CONFIGURATION.** Production
    `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `DATABASE_URL`,
    SMTP credentials per DEPLOYMENT.md; none are committed.

## How the app behaves until each is answered

Fail-closed everywhere: unapproved compatibility ⇒ allocation offers no
bags; missing eligibility config ⇒ `REQUIRES_STAFF_REVIEW`; no required
tests configured ⇒ nothing can be released; no reward rules ⇒ no awards;
mock providers ⇒ nothing pretends to send. The UI labels these states
explicitly (NOT APPROVED, DEMO placeholder, MOCK — DEVELOPMENT ONLY) so
operators cannot mistake defaults for decisions.
