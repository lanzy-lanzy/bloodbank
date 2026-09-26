# Build tasks (tasks.md)

Execution record for the "read plan and build" mandate. Phases completed in
the initial build; remaining items listed honestly.

## Completed

1. ✅ Scaffold: venv (Python 3.11), Django 5.2, app layout (13 apps),
   settings with env-driven config, Tailwind-4-CDN base templates, HTMX +
   Alpine wiring, WhiteNoise, dj-database-url.
2. ✅ Accounts: custom User + roles, login/logout, password reset, failed
   login lockout, user admin (admin only), per-role dashboards.
3. ✅ Audit foundation: immutable AuditLog (3-level), audit service, viewer +
   report.
4. ✅ Donors: registry, soft deletion, screening question model + results,
   eligibility service (config-driven, fail-safe).
5. ✅ Appointments: scheduling, conflict checks + DB constraint, donor
   self-booking.
6. ✅ Donations: state machine, collection workflow (bag creation atomic),
   transition views with reason-required guards.
7. ✅ Inventory: bag lifecycle machine with locking, test results +
   verification, release guard, ledger, expiry job, external-bag
   registration, compatibility engine over approved rules.
8. ✅ Requests: organizations + requester accounts, request lifecycle,
   allocation/issue/cancel, fulfillment feedback (transfuse/return),
   emergency request alerts to staff.
9. ✅ Notifications: templates (variable-validated), provider interface with
   in-app/email/MOCK-SMS, inbox + badge, delivery list + retry, anonymous
   capability-token donor responses with disclaimer.
10. ✅ Rewards: rules/tiers/rewards config, point ledger with running
    balances + idempotent awards, redemption + stock, donor view, admin
    overview, reconciliation command.
11. ✅ Reports: engine (≈20 role-gated reports, filters, pagination), CSV
    export, dashboard charts endpoints.
12. ✅ Settings module: SystemSetting typed accessors, admin quick/full edit,
    blood-bank config UI (blood types/components/test types/compatibility
    with approval visibility).
13. ✅ 80 templates, 0 compile failures; component library incl. Alpine
    confirmation dialogs and HTMX filter forms.
14. ✅ seed_demo command (idempotent, never deletes; DEMO-labelled clinical
    placeholders seeded UNAPPROVED; flags to skip clinical/sample data).
15. ✅ E2E: GET walk 104 pages × 4 roles (+ negatives) ALL OK; POST workflow
    smoke 69 assertions ALL OK (rollback-safe).
16. ✅ Test suite: 106 tests across 12 apps, `manage.py test` OK. Bugs found
    and fixed through these layers: CollectionForm ModelForm misuse,
    brace-in-comment template self-include, queryset audit-delete bypass,
    LoginRequiredMiddleware treating URL *name* as path (login loop),
    password-reset confirm page blocked for anonymous users, retry_count
    semantics.
17. ✅ Documentation set (this file, README, ARCHITECTURE, DATABASE,
    SECURITY, DEPLOYMENT, TESTING, API_ROADMAP, specs, agents, DECISIONS,
    docs/open-questions).

## Remaining / follow-up (not claimed complete)

- ⛔ QR / barcode label printing (design constraints documented; hardware
  unknown → REQUIRES CLARIFICATION).
- ⛔ Real SMS gateway integration (provider interface ready; needs
  credentials + a decision).
- ⛔ Public JSON API (phased plan in API_ROADMAP.md).
- 🟔 Institutional review of every seeded clinical placeholder (compatibility
  matrix approval, thresholds, reward values) — must happen in the UI by the
  operating blood bank, not by this codebase.
- 🟔 Production dry-run deployment + backup/restore drill on staging.
- 🟔 Browser-based spot verification (runserver + manual walkthrough) after
  any further change.
- ♻️ Decide disposition of dev helper scripts `check_templates.py`,
  `check_e2e.py`, `check_e2e_post.py` (keep as documented dev tools — current
  stance — or fold into CI).
