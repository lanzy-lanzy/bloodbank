# Development / test login credentials

> **DEVELOPMENT AND TESTING ONLY.** These are fake accounts created by
> `manage.py seed_demo` against the local SQLite dev database
> (`db.sqlite3`). They are not secrets and exist nowhere real — never reuse
> these usernames or passwords in production, and never add real production
> credentials to this file. See SECURITY.md and `.env.example`.

## Demo accounts (created by `python manage.py seed_demo`)

All four share the same password by default:

| Username    | Password    | Role      | Signs in as / can see                                        |
|-------------|-------------|-----------|--------------------------------------------------------------|
| `admin`     | `Demo12345!` | ADMIN     | Full system: all data, users, system settings, audit logs.   |
| `staff`     | `Demo12345!` | STAFF     | Operations: donors, donations, inventory, requests, test entry, release confirmation. Cannot edit critical system settings. |
| `donor`     | `Demo12345!` | DONOR     | Donor Diana Donor (`DON-000001`) only: own profile, appointments, donations, rewards, notifications. |
| `requester` | `Demo12345!` | REQUESTER | Demo Provincial Hospital only: own organization's blood requests and allocations. |

Each role lands on its own dashboard after login at
<http://127.0.0.1:8000/accounts/login/>.

Notes:

- `seed_demo` is idempotent and insert-only; re-running it never deletes or
  changes existing data.
- Choose a different demo password at seed time with
  `python manage.py seed_demo --password "YourChoice123!"`.
- The remaining demo donors (Maria Clara, Juan Dela Cruz, …) are donor
  *profiles* with no user account — staff/admin manage them; only `donor`
  above can log in as a donor.
- Password logins lock temporarily after repeated failures
  (`FAILED_LOGIN_LIMIT` / `FAILED_LOGIN_LOCKOUT_SECONDS` in `.env`) — the
  defaults are generous enough for manual testing.

## Automated-test accounts

The `manage.py test` suites never touch the dev database (they use a
disposable test DB built by `core/testing.py` factories). Those tests create
users on the fly with the fixed password:

```
Test12345!
```

Test usernames follow patterns like `don-staff3`, `test-donor`, `admin1` and
only exist inside the rolled-back test run — you cannot log in with them
anywhere.

## Django superuser

`seed_demo` creates `admin` with `is_superuser=True` (so Django admin at
`/django-admin/` also works, password `Demo12345!`). For a production-style
superuser use `python manage.py createsuperuser` and supply real, private
credentials — never commit or write them down here.

## Where real secrets live (and never do)

- Environment: `.env` (git-ignored) — `SECRET_KEY`, `DATABASE_URL`, etc.
  `.env.example` documents every variable with placeholder values.
- Never: hard-coded fallbacks in settings, credentials in templates or this
  file beyond the demo fakes above, or committed `.env`.
