# API Roadmap

The system today is deliberately **server-rendered only** (Django templates +
HTMX fragments). There is no public JSON API yet, and none is claimed. This
document records the planned path so future integrations (hospital HIS,
laboratory LIS, mobile donor app) are designed with the same safety rules.

## Principles any API must inherit

1. **Same services, no bypass.** Endpoints must call the existing service
   layer (`InventoryService`, `BloodRequestService`, …) so state machines,
   release guards, locking and audit stay enforced. No endpoint may write a
   status column directly.
2. **AuthN/AuthZ parity.** Token scope mirrors the four roles exactly;
   requester tokens see only their organization; donor tokens only themself.
3. **Fail-safe semantics preserved.** Unconfigured compatibility/eligibility
   returns an explicit `requires_configuration` outcome — never an inferred
   clinical default.
4. **PII minimisation.** List endpoints return codes and statuses; names and
   medical values only from detail endpoints with the same visibility rules
   as the web UI. Public label endpoints expose only `bag_code`.

## Planned phases

### Phase 1 — read-only reporting API (lowest risk)
- `GET /api/v1/inventory/summary/` — counts by blood type × component ×
  status (drives external dashboards/screens).
- `GET /api/v1/reports/<key>/` — the existing ~20 reports as JSON (same
  role gating as the web reports center) + CSV passthrough.
- Auth: Django session or static service token provisioned per consumer,
  stored in env — never in the repo.

### Phase 2 — event webhooks (outbound)
- Bag released / bag expiring / request approved / emergency alert sent →
  signed POST to a consumer endpoint (HMAC key from env). Delivery attempts
  are audited; failed deliveries visible in the notification/delivery UI.

### Phase 3 — laboratory result ingestion (careful)
- `POST /api/v1/bags/<bag_code>/test-results/` with a service-scoped token:
  writes `TestResult` as PENDING/performed rows **without** verification —
  verification stays a human staff action in the UI (the release guard's
  `verified_by` requirement must never be satisfiable by an automated token
  until policy explicitly changes it).
- Requires per-test type mapping configuration (LIS code ↔ TestType).
  **REQUIRES CLARIFICATION**: which LIS, transport (HL7/FHIR/CSV), and
  result vocabulary.

### Phase 4 — donor mobile endpoints
- `GET /api/v1/me/donations|appointments|rewards`, appointment self-book
  (same rules as the web form: forced REQUESTED, future-only, conflict
  check). OAuth2/OIDC or scoped device tokens — decision deferred until a
  concrete client exists. **REQUIRES CLARIFICATION.**

### Explicitly NOT planned
- Public write access to blood-bank configuration (compatibility rules,
  required tests, eligibility thresholds): stays interactive-admin-only.
  A machine-editable compatibility endpoint would undercut the
  approved-by-a-human guarantee.
- Any endpoint exposing donor identities per bag beyond staff roles.

## Notes for implementers
- Versioned path (`/api/v1/`), DRF or plain Django JSON views — decide when
  Phase 1 starts; nothing in the current codebase depends on DRF.
- Rate limiting + lockout parity with web login (`FAILED_LOGIN_*` envs).
- Add API tests to each app mirroring the existing permission matrix tests.
