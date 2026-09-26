# Deployment

Development runs on SQLite with defaults from `.env`. Production targets
PostgreSQL behind Gunicorn/Uvicorn + a reverse proxy (HTTPS).

## Checklist

1. **Separate database.** Create a PostgreSQL role/database; never reuse the
   dev `db.sqlite3`.

   ```sql
   CREATE ROLE bloodbank LOGIN PASSWORD '...';   -- use a secrets manager value
   CREATE DATABASE bloodbank OWNER bloodbank;
   ```

2. **Environment** (all secrets via env / `.env`, never committed):

   ```
   DEBUG=False
   SECRET_KEY=<long random string>            # e.g. django-admin generate-secret-key
   ALLOWED_HOSTS=bloodbank.example.org
   CSRF_TRUSTED_ORIGINS=https://bloodbank.example.org
   DATABASE_URL=postgres://bloodbank:***@db:5432/bloodbank
   TIME_ZONE=Asia/Manila
   SESSION_TIMEOUT_SECONDS=1800
   EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
   EMAIL_HOST=... EMAIL_HOST_USER=... EMAIL_HOST_PASSWORD=... EMAIL_USE_TLS=True
   SMS_PROVIDER=semaphore                     # or "mock" for development only
   SMS_API_KEY=                               # Semaphore key — env only, never commit
   SMS_SENDER_NAME=                           # registered sender name/number
   ```

   (`MEDIA_ROOT` is fixed at `<project>/media` in settings — place the whole
   project on durable storage or patch settings before deployment.)

3. **Static files.** WhiteNoise is wired in; run
   `python manage.py collectstatic` at deploy time.

4. **Migrations.** `python manage.py migrate` — additive schema only.
   **Never** run `migrate` with `--fake`/reset on the production DB; take a
   `pg_dump` before upgrading. This codebase contains no destructive
   migrations by policy.

5. **Application server** (example):

   ```
   gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3
   ```

   nginx/Caddy terminates TLS and proxies; serve `/media/` from the
   protected directory (or via authorized Django views, not static aliasing
   — request documents may contain patient references).

6. **Scheduled jobs** (cron example):

   ```
   0 1 * * *  python manage.py expire_blood_bags          # AVAILABLE→EXPIRED + alerts
   30 1 * * * python manage.py send_expiration_alerts     # near-expiry notifications (MOCK channels in dev)
   0 2 * * *  python manage.py cleanup_old_sessions
   15 2 * * 0 python manage.py recalculate_reward_balances  # ledger↔balance audit
   ```

7. **First production data configuration (REQUIRES CONFIGURATION — do not
   skip):**
   - Log in as `admin`; open **Settings → Blood Bank Configuration**.
   - Review/replace or confirm every seeded demo value: blood types,
     components + shelf lives, required tests, eligibility thresholds,
     reward rules/tiers, low-stock/expiring thresholds.
   - **Approve the compatibility matrix explicitly** (seeded rows are
     `approved_by = NULL` / marked NOT APPROVED). Until approved rules exist,
     allocation stays fail-safe (nothing is considered compatible).
   - Create real staff/donor/requester accounts; rotate or delete the demo
     accounts (`seed_demo` never deletes — do it deliberately in the UI).

8. **Backup/DR:** nightly `pg_dump`, offsite copies, restore drill. Audit
   log is append-only — backups are the recovery mechanism, nothing else.

## Upgrade flow

```
git pull → uv sync --extra deploy → npm ci && npm run build:css → manage.py migrate
→ manage.py collectstatic → restart gunicorn → smoke: login, dashboard,
one request→allocate→issue chain in a staging copy first
```

## Monitoring hooks

- Structured log lines (`bloodbank.*` loggers): LOGIN_SUCCESS/FAILED,
  BAG transitions, notification dispatch failures.
- `/health` style check: `manage.py check` at boot; consider adding a
  lightweight authenticated heartbeat to reports later (API_ROADMAP.md).
