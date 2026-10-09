# Testing

Three verification layers, all runnable locally and non-destructive to the
development database.

## 1. Django test suite (authoritative)

```bash
python manage.py test                 # 218 tests, ~5-6 min
python manage.py test inventory       # one app
python manage.py test audit.tests.AuditImmutabilityTests -v 2
```

The runner builds a throw-away test database (`*_test`); the dev
`db.sqlite3` is never touched.

Coverage by app (unit + integration + permission/security):

| App | What is proven |
|---|---|
| core | Styled-form machinery, pagination component render (incl. the brace-in-comment regression guard + automated scan of all templates), **CSRF enforced on login POST**, anonymous login/reset pages reachable (middleware path-vs-name regression) |
| accounts | Dashboard for each of the 4 roles, anonymous redirect, admin-only user list, admin role works without superuser, hashed passwords; self-registration: inactive user + PENDING request + admin in-app alert created, ADMIN/STAFF role escalation refused at form AND service, duplicate username/email (case-insensitive) and non-PH mobile rejected, requester needs organization; review: approve activates + notifies (in-app+SMS channels), reject needs reason + REJECT token re-checked server-side, one-shot re-review refused, self-review refused, pending/rejected login messages only after password match (no status leak, wrong password stays generic), registration pages admin-only for staff/donor/requester/anonymous |
| audit | Append-only: instance update refused, instance delete refused, **bulk queryset delete refused**, snapshot capture |
| donors | Eligibility engine: configured-rules eligible, missing interval ⇒ REQUIRES_STAFF_REVIEW (fail-safe), interval deferral with next-eligible date, permanent-deferral state, active temp-deferral window, rolling-year cap, age bounds; soft deletion hides from default manager; donor cannot read other donors or the directory |
| appointments | Slot conflict + DB-level unique open-slot constraint, past-date rejection (model + self-book form), self-book forced to REQUESTED, staff list blocked for donors |
| donations | Transition machine rejects illegal jumps, collection requires APPROVED, collection creates QUARANTINED bag + closes appointment, view-level guard: DEFERRED without reason refused |
| inventory | State-machine refusals, terminal states, ledger+audit written, release guard matrix (no required tests configured / missing / unverified / reactive ⇒ blocked; complete verified non-reactive ⇒ released), expired bags cannot re-enter stock, expiry job scope, compatibility engine: no rules = not compatible, rule grants, disallowed rule, component scoping, inactive rule ignored, bag code uniqueness; **open-demand panel** (shortage measured against free stock with live reservations counted as in-flight, unapproved requests excluded, covered items leave the panel, unconfigured rules surfaced not guessed), the one window source for expiring counts, stale-but-usable stock never offered as inventory and warned about on the dashboard, `attention_count` composition, badge endpoint staff-only (donor/requester denied), bag list combined-status + in-date/expiring filters, Allocated-to column names the owning request; **bag detail is a page, not a modal** — an HX-Request still returns a full document (so a reintroduced modal link fails loudly instead of half-rendering), the working surfaces and plain-POST forms are present, no template binds a `bag_detail` link to `hx-get`, and it stays staff-only; **Inventory Statement** (print preview + PDF) — renders the letterhead/signature block, lists every bag, honours the bag filters, its counts match the dashboard's, it states the *configured* expiry window, it warns about usable-but-past-expiry stock, the PDF is a real attachment, and requester/donor are refused on both endpoints |
| requests | Submit requires items, DRAFT→SUBMITTED→UNDER_REVIEW→APPROVED chain, reject freezes, allocate requires APPROVED + compatible + AVAILABLE, one-active-allocation-per-bag, issue updates counters, transfuse moves bag but allocation stays ISSUED, cancel returns bag to stock, requester org-scoping + cannot approve; **reservation quota** (second reservation refused while the first is outstanding, issued bags block further reserve, cancelling a reservation reopens the slot, multi-unit item holds exactly its quantity, detail page shows the "all reserved" state instead of a form), requester notified on approve and on reject (reason carried), **walk-in intake** (desk organization comes only from `walk_in_organization_id` — unset or inactive desk refuses with REQUIRES CONFIGURATION; counter contact required at form and model level; the desk cannot be picked on the organization channel; a requester POST carrying a forged channel stays an organization record for its own org; walk-in rows hidden from a desk-organization requester in list, detail (403) and sidebar count; staff channel filter; staff record transfusion on an issued walk-in; **walk-in has no approval queue** — created straight as APPROVED with `REQUEST_VALIDATED_AT_COUNTER` audit, a walk-in draft's submit validates it, `approve()`/`reject()` refuse a walk-in row, `validate_walk_in()` refuses non-walk-in rows, no requester notification is sent, the detail page renders no approve/reject control, and cancel still closes it; **Walk-in Desk workspace** — staff-only page lists only counter records with the contact, flags an unconfigured desk with REQUIRES CONFIGURATION, Open/Closed/All scope tabs split the queue, the badge counts records owing bags, and requester/donor get 403 on both page and badge, with the sidebar link rendered for staff only) |
| notifications | Provider dispatch (in-app always; email via locmem; missing address fails gracefully), retry only for FAILED + retry_count increments, anonymous token respond works + disclaimer present + idempotent + invalid value rejected, pk-path requires owner, foreign donor cannot mark read |
| rewards | Ledger running_balance/balance cache, negative balance refused, awards only from configured rules + idempotent per reference, tier progression/next-tier, redemption: insufficient points, stock decrement, out-of-stock, donor blocked from admin overview |
| settings_app | Typed get/set round-trips, missing key defaults, changes audited, admin can quick-set, staff cannot (value unchanged), staff/anon blocked from settings pages |
| reports | Role gating (staff shared report 200, audit report admin-only 403 for staff, donor blocked), unknown key denied, CSV export content-type + row integrity, inventory report renders data; **printable documents** — preview renders letterhead + confidentiality + real rows + a toolbar linking the other two outputs, active filters carried into the export links while `?page=` is stripped, admin-only report stays un-printable for staff, donor/donor-PDF 403, unknown key denied; **PDF export** — `application/pdf`, attachment disposition, `%PDF-`…`%%EOF` payload, title present, timestamped filename, same role gate; every report has a description/group, wide reports flagged landscape, filter labels rendered as operator words; column inference (numeric → right-aligned, placeholders ignored, widths sum to 100%, no column starved); `pdf_safe_text` folds arrows/typography and keeps accents |
| core (documents) | Shared printable-document engine: one `DocumentSpec` drives preview, print and PDF; toolbar is absent from the PDF render (`chrome=False`); `DocumentRenderError` degrades to the preview instead of returning a non-PDF body |

## 2. Template compile + GET walk

```bash
python check_templates.py   # compiles all templates (fail-fast syntax)
python check_e2e.py         # 4-role GET walk: dashboards, every list with its
                            # filters, every detail, every form, all permitted
                            # report pages + CSV/preview/PDF exports
python check_documents.py   # every report + the inventory statement: print
                            # preview 200, a real %PDF- body, and 403 for the
                            # roles that must not have them
```

`check_e2e.py` walks every role's actual pages (dashboards, every list with
its filters, every detail, every form, all permitted report pages + CSV
exports) asserting expected status codes — including that donors/requesters
get 403/redirects on pages they must not see, and that anonymous capability
links render with the non-medical disclaimer.

## 3. POST workflow smoke (rollback-safe)

```bash
python check_e2e_post.py    # 75 assertions, ALL OK
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
- `manage.py reconcile_inventory` is the read-only integrity net for the
  request↔inventory bridge (RESERVED allocations on closed requests,
  `fulfilled_quantity > quantity`, reserved+fulfilled over the item's
  quantity). It reports, never repairs.
- When adding safety-relevant behaviour, extend the release-guard /
  compatibility / permission matrices — those carry the clinical-fail-safe
  guarantees.
