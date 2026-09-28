# Blood Bank Management System — Tambulig

![Django](https://img.shields.io/badge/Django-5.2-092E20?logo=django&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![HTMX](https://img.shields.io/badge/HTMX-2.0-1e66f5)
![Tailwind](https://img.shields.io/badge/Tailwind_CSS-4-06B6D4?logo=tailwindcss&logoColor=white)
![Tests](https://img.shields.io/badge/tests-106%20passing-brightgreen)

A production-oriented web system for managing the full blood-bank workflow:
donor registration → screening → donation → collection → testing/safety
screening → blood bag inventory → requests → compatibility → release →
emergency donor notification → rewards → reports → audit logs.

Built with **Django 5.2 + Django Templates + HTMX + Alpine.js + Tailwind CSS 4**
(no React/Vue/Angular). SQLite for development, PostgreSQL for production.

> **Safety posture:** this system stores and enforces *institution-configured*
> rules. It contains **no hard-coded clinical logic** — blood types,
> components, required tests, compatibility pairs, eligibility thresholds and
> reward values are all database-configured, and demo values are clearly
> labelled as placeholders requiring institutional approval. See
> [SECURITY.md](SECURITY.md) and [DECISIONS.md](DECISIONS.md).

## Table of contents

- [Quick start (development)](#quick-start-development)
- [Feature highlights](#feature-highlights)
- [What is where](#what-is-where)
- [Documentation](#documentation)
- [Verification status](#verification-status)
- [Deliberately NOT claimed](#deliberately-not-claimed)

## Quick start (development)

The environment is managed with [uv](https://docs.astral.sh/uv/): dependencies
are declared in `pyproject.toml` and pinned in `uv.lock`, and `uv` provisioned
CPython 3.11.12 itself.

```bash
uv sync                           # creates .venv with uv-managed Python 3.11 + locked deps
.venv\Scripts\activate            # Windows; on Linux/macOS: source .venv/bin/activate
npm install                       # local Tailwind CSS 4 toolchain (no CDN)
npm run build:css                 # compiles static/css/input.css -> static/css/tailwind.css
copy .env.example .env            # then edit SECRET_KEY etc.
python manage.py migrate
python manage.py seed_demo        # DEMO config + demo accounts (idempotent, never deletes)
python manage.py runserver
```

Tailwind CSS 4 is built locally (no CDN): keep `npm run watch:css` running in a
second terminal while editing templates so `static/css/tailwind.css` rebuilds
on change. The compiled file is committed, so the app also runs without npm.

For production installs use an extra: `uv sync --extra postgres` (driver only)
or `uv sync --extra deploy` (adds gunicorn). `requirements.txt` is kept as a
convenience mirror for `pip install -r requirements.txt`, but
`pyproject.toml` + `uv.lock` are the source of truth — update both together.

Demo accounts created by `seed_demo` (password `Demo12345!` by default,
override with `--password`): `admin`, `staff`, `donor`, `requester` —
see [credentials.md](credentials.md) for what each role can do.
Run `python manage.py seed_demo --help` for flags — e.g. `--no-clinical-demo`
seeds configuration WITHOUT the placeholder eligibility thresholds and
compatibility rules, leaving the system in its fail-safe state
(`REQUIRES STAFF REVIEW` / nothing compatible) until an administrator
approves real values.

Open <http://127.0.0.1:8000/> and log in. Each role lands on its own dashboard.

## Feature highlights

- **Role-based dashboards** for ADMIN / STAFF / DONOR / REQUESTER, each scoped
  to only the data that role may see.
- **Public self-registration** for donors/requesters at `/register/` with an
  admin approval gate: accounts stay inactive until reviewed, and applicants
  are told pending/rejected status (plus the reason) at login and by
  in-app/SMS notification.
- **Full donation lifecycle** as an enforced state machine: registration →
  screening → collection → testing → release.
- **Blood-bag inventory** with a safety-testing ledger, expiry jobs and a
  release guard that blocks unapproved units.
- **Database-driven compatibility engine** — no hard-coded clinical rules; the
  app fails safe (nothing compatible / `REQUIRES STAFF REVIEW`) when
  unconfigured.
- **Blood requests** from hospitals/organizations with allocation and
  fulfillment feedback, plus a **Walk-in Desk** for patients who come to the
  blood bank directly (staff-only workspace at `/requests/walk-ins/`, booked
  against a configured desk organization, invisible to requester accounts). A
  walk-in is a direct clinic request — no approve/reject queue; it is validated
  at the counter and filled from compatible bags on hand.
- **Emergency donor notification** with token-based responses over in-app,
  email and a clearly-labelled **MOCK** SMS provider.
- **Rewards program**: immutable point ledger, configurable rules, tiers and
  redemption.
- **Config-driven reports** with CSV export.
- **Append-only audit trail** across all protected state changes.

## What is where

| Path | Purpose |
|---|---|
| `config/` | Settings, root URLs, WSGI/ASGI |
| `core/` | Base templates, components, mixins, styled-form machinery, dashboards, management commands (`seed_demo`, `cleanup_old_sessions`) |
| `accounts/` | Custom `User` with roles ADMIN / STAFF / DONOR / REQUESTER, login, lockout, user admin |
| `donors/` | Donor registry, screening questions/results, eligibility engine |
| `appointments/` | Donation appointment scheduling + donor self-booking |
| `donations/` | Donation lifecycle state machine, collection workflow |
| `inventory/` | Blood bags, test results, state machine, release guard, ledger, compatibility engine, expiry jobs |
| `requests/` | Organizations, requester accounts, blood requests, allocation, fulfillment feedback |
| `notifications/` | Templates, providers (in-app / Django email / **MOCK** SMS), emergency alerts, token-based donor responses |
| `rewards/` | Point ledger, configured rules, tiers, redemption |
| `reports/` | Config-driven report engine + CSV export |
| `settings_app/` | System settings + blood-bank configuration UI (admin-only) |
| `audit/` | Append-only audit trail (immutable model + queryset) |
| `templates/` | All Django templates (per-app folders + `components/`) |
| `check_*.py` | Dev-only verification scripts (see [TESTING.md](TESTING.md)) |

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — module map, state machines, service layer
- [DATABASE.md](DATABASE.md) — data model and key constraints
- [SECURITY.md](SECURITY.md) — roles, data scoping, audit, fail-safe defaults
- [DEPLOYMENT.md](DEPLOYMENT.md) — production checklist (PostgreSQL, secrets, HTTPS)
- [TESTING.md](TESTING.md) — how to run tests + what is covered
- [API_ROADMAP.md](API_ROADMAP.md) — future read-only/reporting API plan
- [specs.md](specs.md) / [tasks.md](tasks.md) / [agents.md](agents.md) — requirements analysis
- [DECISIONS.md](DECISIONS.md) — decision log
- [docs/open-questions.md](docs/open-questions.md) — **REQUIRES CLARIFICATION** items

## Verification status

- `python manage.py test` — 218 tests, all passing (services, state machines,
  permissions, security guards).
- Template compile check + a 142-page GET walk across all four roles plus an
  anonymous public/private walk + a
  75-assertion POST workflow smoke (runs inside a transaction that is always
  rolled back). See [TESTING.md](TESTING.md).

## Deliberately NOT claimed

- No real SMS/email delivery is configured; SMS is a **MOCK — DEVELOPMENT
  ONLY** provider and email defaults to the console backend.
- Clinical values (eligibility windows, compatibility matrix, deferral rules,
  reward points) are **demo placeholders** seeded UNAPPROVED — a real blood
  bank must review and replace them via the settings UI.
- QR / barcode label printing is **not yet implemented** (tracked in
  [API_ROADMAP.md](API_ROADMAP.md)). When added, labels must encode only the
  public bag code (`BB-…`) and never donor identifiers.
