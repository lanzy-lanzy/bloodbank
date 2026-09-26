# Testing

Three verification layers, all runnable locally and non-destructive to the
development database.

## 1. Django test suite (authoritative)

```bash
python manage.py test                 # 106 tests, ~2-3 min
python manage.py test inventory       # one app
python manage.py test audit.tests.AuditImmutabilityTests -v 2
```

The runner builds a throw-away test database (`*_test`); the dev
`db.sqlite3` is never touched.

Coverage by app (unit + integration + permission/security):

| App | What is proven |
|---|---|
| core | Styled-form machinery, pagination component render (incl. the brace-in-comment regression guard + automated scan of all templates), **CSRF enforced on login POST**, anonymous login/reset pages reachable (middleware path-vs-name regression) |
| accounts | Dashboard for each of the 4 roles, anonymous redirect, admin-only user list, admin role works without superuser, hashed passwords |
| audit | Append-only: instance update refused, instance delete refused, **bulk queryset delete refused**, snapshot capture |
| donors | Eligibility engine: configured-rules eligible, missing interval ⇒ REQUIRES_STAFF_REVIEW (fail-safe), interval deferral with next-eligible date, permanent-deferral state, active temp-deferral window, rolling-year cap, age bounds; soft deletion hides from default manager; donor cannot read other donors or the directory |
| appointments | Slot conflict + DB-level unique open-slot constraint, past-date rejection (model + self-book form), self-book forced to REQUESTED, staff list blocked for donors |
| donations | Transition machine rejects illegal jumps, collection requires APPROVED, collection creates QUARANTINED bag + closes appointment, view-level guard: DEFERRED without reason refused |
| inventory | State-machine refusals, terminal states, ledger+audit written, release guard matrix (no required tests configured / missing / unverified / reactive ⇒ blocked; complete verified non-reactive ⇒ released), expired bags cannot re-enter stock, expiry job scope, compatibility engine: no rules = not compatible, rule grants, disallowed rule, component scoping, inactive rule ignored, bag code uniqueness |
| requests | Submit requires items, DRAFT→SUBMITTED→UNDER_REVIEW→APPROVED chain, reject freezes, allocate requires APPROVED + compatible + AVAILABLE, one-active-allocation-per-bag, issue updates counters, transfuse moves bag but allocation stays ISSUED, cancel returns bag to stock, requester org-scoping + cannot approve |
| notifications | Provider dispatch (in-app always; email via locmem; missing address fails gracefully), retry only for FAILED + retry_count increments, anonymous token respond works + disclaimer present + idempotent + invalid value rejected, pk-path requires owner, foreign donor cannot mark read |
| rewards | Ledger running_balance/balance cache, negative balance refused, awards only from configured rules + idempotent per reference, tier progression/next-tier, redemption: insufficient points, stock decrement, out-of-stock, donor blocked from admin overview |
| settings_app | Typed get/set round-trips, missing key defaults, changes audited, admin can quick-set, staff cannot (value unchanged), staff/anon blocked from settings pages |
| reports | Role gating (staff shared report 200, audit report admin-only 403 for staff, donor blocked), unknown key denied, CSV export content-type + row integrity, inventory report renders data |

## 2. Template compile + GET walk

```bash
python check_templates.py   # compiles all 80 templates (fail-fast syntax)
python check_e2e.py         # 104 pages across admin/staff/donor/requester
                            # sessions + negative-access checks + token links
```

`check_e2e.py` walks every role's actual pages (dashboards, every list with
its filters, every detail, every form, all permitted report pages + CSV
exports) asserting expected status codes — including that donors/requesters
get 403/redirects on pages they must not see, and that anonymous capability
links render with the non-medical disclaimer.

## 3. POST workflow smoke (rollback-safe)

```bash
python check_e2e_post.py    # 69 assertions, ALL OK
```

Runs the real view endpoints for the full lifecycle — donation→screening→
approval→collection→testing→verification→release, request→review→approve→
allocate→issue→transfuse, notification token responses/retry, appointment
self-booking, reward redemption, admin quick-set, audit immutability —
**inside `transaction.atomic()` that always raises a Rollback exception**,
so the dev database is byte-for-byte unchanged afterwards (it verifies real
state transitions without persisting demo noise).

This layer found three production-relevant bugs during the build: a
ModelForm misuse that 500'd every collection POST, tag-bearing template
comments self-including `pagination.html`, and queryset-level audit deletes
bypassing immutability (all now fixed and covered by proper tests above).

## Notes

- The `check_*.py` scripts are development helpers, not CI requirements;
  `manage.py test` is the source of truth. They depend on seeded demo
  accounts (`admin/staff/donor/requester`, password `Demo12345!`) from
  `manage.py seed_demo`.
- `manage.py test` deliberately exercises services, not providers: email is
  patched to locmem in notification tests; SMS stays on the labelled mock.
- When adding safety-relevant behaviour, extend the release-guard /
  compatibility / permission matrices — those carry the clinical-fail-safe
  guarantees.
