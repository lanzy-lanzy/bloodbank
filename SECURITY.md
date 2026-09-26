# Security

## Roles and data scoping

| Role | Scope |
|---|---|
| ADMIN | Everything, incl. system settings, blood-bank configuration, audit trail, user management, admin reports |
| STAFF | Operational workflow: donors, screening, donations, collection, testing, inventory, requests/approval/allocation/issue, notifications, rewards granting, most reports. **Cannot** change critical configuration (enforced by `AdminRequiredMixin` on every settings/blood-bank/template-management view) |
| DONOR | Own profile (limited self-edit), own donations/appointments/rewards/notifications. Never another donor's data |
| REQUESTER | Requests of **their own organization** only; fulfillment feedback. No access to donors, inventory or reports |

Enforcement is server-side in mixins/views (`StaffRequiredMixin`,
`AdminRequiredMixin`, `_get_request_or_403` organization scoping, donor
ownership checks on notification read/respond). UI hiding is cosmetic only;
every check was verified with direct HTTP requests (see TESTING.md —
negative-access assertions in both the GET walk and the test suite).

Donor self-edit (`DonorSelfProfileForm`) exposes contact fields only —
medical fields are staff-maintained. Donor dashboards show only the donor's
own eligibility/rewards.

## Authentication & session

- Custom `accounts.User` (no username enumeration beyond login error text);
  Django password hashing; `AUTH_PASSWORD_VALIDATORS` with configurable
  `PASSWORD_MIN_LENGTH`.
- Failed-login lockout (`BloodBankAuthenticationForm`): after
  `FAILED_LOGIN_LIMIT` (default 5) failures an account is locked for
  `FAILED_LOGIN_LOCKOUT_SECONDS` (default 900 s) — login attempts and
  failures are logged server-side.
- `SESSION_COOKIE_AGE` from env (default 1800 s). In production also set
  `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE` (enabled automatically when
  `DEBUG=False`).
- Password reset via standard Django flows (console email in dev —
  production must configure a real `EMAIL_BACKEND`).

## CSRF, XSS, clickjacking, hosts

- `CsrfViewMiddleware` on every POST (no `csrf_exempt` anywhere); a test
  asserts anonymous login POST without a token is rejected 403.
- Django auto-escaping in all templates; no `|safe` usage on user data.
- `XFrameOptionsMiddleware` default DENY.
- `ALLOWED_HOSTS` from env; production requires real values.

## Audit trail (immutable)

Every significant action writes `AuditLog` (actor, action, module, object
reference, before/after snapshots, IP, description): logins, donor CRUD,
screenings, donation transitions, collection, test recording/verification,
bag transitions, releases, allocations/issues, notification
dispatch/retries, reward posts/redemptions, setting changes, emergency
notifies. Immutability is enforced at three levels — instance `save()`
refuses updates to an existing row, instance `delete()` raises, and
`ImmutableAuditQuerySet.delete()/update()` raise (queryset bulk delete would
otherwise silently bypass the per-instance guard — this was found and fixed
by the test suite).

Soft deletion (donors) preserves history; nothing in the app truncates or
mass-deletes records.

## Fail-safe clinical posture (anti-hallucination policy)

- **Compatibility:** absence of an explicit active rule = NOT compatible.
  Nothing is ever inferred from textbooks at runtime; the demo ABO/Rh matrix
  is seeded with `approved_by = NULL` and the UI marks rows **NOT APPROVED**
  until an administrator approves them.
- **Release:** a bag becomes AVAILABLE only when every active REQUIRED test
  has a latest NON_REACTIVE result that a human verified. Zero required
  tests configured ⇒ nothing can be released (configuration is demanded,
  not assumed).
- **Eligibility:** missing/invalid configured thresholds ⇒
  `REQUIRES_STAFF_REVIEW` — the system refuses to invent clinical cutoffs.
- **Rewards:** event awards happen only when a `RewardRule` row exists;
  unconfigured ⇒ no award (no invented point values at runtime).
- Bag status starts QUARANTINED; collection never auto-releases anything.

## PII and external surfaces

- Donor contact details and medical results are visible only to staff/admin
  (and the donor themself); requester views show patient reference only.
- Publicly reachable surfaces: login/reset flows and
  `/notifications/respond/<uuid-token>/`. The token is an unguessable UUID
  capability that can record only one of four availability responses for
  one notification, first-write-wins, with a visible disclaimer that a
  response is **not a medical clearance** (enforced server-side, asserted in
  tests). No names, blood types or clinical data are rendered on the
  anonymous page beyond the alert body itself.
- Bag codes (`BB-YYYY-NNNNNN`) are the only label-safe identifier; QR /
  barcode rendering is not yet implemented and any future label must encode
  only this public code.
- Uploads: `supporting_document` restricted by extension validator and size
  setting; served under `MEDIA_ROOT` (protect this directory in production —
  see DEPLOYMENT.md).

## External integrations

- Email: Django mail framework; default backend is **console** (dev).
  Nothing claims real delivery in dev.
- SMS: `MockSMSProvider` — **MOCK — DEVELOPMENT ONLY**, logs to the audit
  log/server log and clearly labelled in UI. No real SMS gateway is
  configured or implied.
- Secrets (API keys, DB URLs, SECRET_KEY) come only from environment /
  `.env`; none are hard-coded. `.env.example` documents every variable;
  `.env` itself is git-ignored.
