"""
Django settings for the Blood Bank Management System.

Development: MySQL (XAMPP). Production: PostgreSQL via DATABASE_URL.
SQLite remains available with DB_ENGINE=sqlite.
All secrets come from environment variables (.env supported).
"""
import os
import sys
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

# Development targets the MySQL/MariaDB service bundled with XAMPP. Django's
# MySQL backend imports the driver under the name "MySQLdb"; PyMySQL is a
# pure-Python drop-in (no compiler needed on Windows) registered here for it.
# Guarded so the file-based SQLite fallback still runs when PyMySQL is absent.
try:
    import pymysql

    pymysql.install_as_MySQLdb()
except ImportError:  # pragma: no cover - SQLite-only setups have no MySQL driver
    pass

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes", "on")


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# --- Core -------------------------------------------------------------------
SECRET_KEY = os.environ.get("SECRET_KEY", "insecure-dev-key-change-me")
DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Local apps
    "core.apps.CoreConfig",
    "accounts.apps.AccountsConfig",
    "audit.apps.AuditConfig",
    "donors.apps.DonorsConfig",
    "appointments.apps.AppointmentsConfig",
    "donations.apps.DonationsConfig",
    "inventory.apps.InventoryConfig",
    "requests.apps.RequestsConfig",
    "notifications.apps.NotificationsConfig",
    "rewards.apps.RewardsConfig",
    "reports.apps.ReportsConfig",
    "settings_app.apps.SettingsAppConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.LoginRequiredMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site_settings",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Database ---------------------------------------------------------------
# Default: the local MySQL/MariaDB server provided by XAMPP
# (127.0.0.1:3306, user "root", empty password, database "bloodbank").
# Every value is overridable through the DB_* variables in .env. A
# DATABASE_URL still takes precedence so production PostgreSQL keeps working
# unchanged; set DB_ENGINE=sqlite to fall back to the file-based dev database.
_DB_ENGINE = os.environ.get("DB_ENGINE", "mysql")
_DB_CONN_MAX_AGE = int(os.environ.get("DB_CONN_MAX_AGE", "0"))

if os.environ.get("DATABASE_URL"):
    DATABASES = {
        "default": dj_database_url.config(
            conn_max_age=_DB_CONN_MAX_AGE,
        )
    }
elif _DB_ENGINE == "sqlite":
    DATABASES = {
        "default": dj_database_url.config(
            default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
            conn_max_age=_DB_CONN_MAX_AGE,
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.environ.get("DB_NAME", "bloodbank"),
            "USER": os.environ.get("DB_USER", "root"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DB_PORT", "3306"),
            "CONN_MAX_AGE": _DB_CONN_MAX_AGE,
            "OPTIONS": {
                "charset": "utf8mb4",
                # XAMPP ships MariaDB, which does not enable strict mode by
                # default. Turn it on per connection so bad writes (e.g. data
                # truncation) raise instead of passing silently (mysql.W002).
                "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            },
        }
    }

# MySQL/MariaDB cannot build partial (conditional) unique indexes, so Django
# reports models.W036 for the two constraints this project relies on. Both are
# re-created on MySQL as generated-column + UNIQUE-index migrations
# (appointments 0003_mysql_open_slot_unique, requests
# 0004_mysql_active_allocation_unique), so the warning is expected there and
# silenced for that backend only. SQLite/PostgreSQL still surface W036 for any
# conditional constraint that lacks such a migration.
if DATABASES["default"]["ENGINE"] == "django.db.backends.mysql":
    SILENCED_SYSTEM_CHECKS = ["models.W036"]

# --- Auth -------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": int(os.environ.get("PASSWORD_MIN_LENGTH", "8"))}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

# Session timeout (seconds). Configurable via env; default 30 minutes.
SESSION_COOKIE_AGE = int(os.environ.get("SESSION_TIMEOUT_SECONDS", "1800"))
SESSION_SAVE_EVERY_REQUEST = env_bool("SESSION_SAVE_EVERY_REQUEST", True)

# Failed-login protection: lock account after N failures for M seconds.
FAILED_LOGIN_LIMIT = int(os.environ.get("FAILED_LOGIN_LIMIT", "5"))
FAILED_LOGIN_LOCKOUT_SECONDS = int(os.environ.get("FAILED_LOGIN_LOCKOUT_SECONDS", "900"))

# --- I18N -------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "Asia/Manila")
USE_I18N = True
USE_TZ = True

# --- Static / media -----------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
# The manifest backend requires collectstatic; skip it in development (DEBUG)
# and under `manage.py test` so pages render straight from static/.
if DEBUG or "test" in sys.argv:
    STORAGES["staticfiles"] = {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    }

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# File upload constraints (bytes / extensions). Executables are rejected.
MAX_UPLOAD_SIZE_MB = int(os.environ.get("MAX_UPLOAD_SIZE_MB", "10"))
ALLOWED_UPLOAD_EXTENSIONS = env_list(
    "ALLOWED_UPLOAD_EXTENSIONS", ".pdf,.png,.jpg,.jpeg,.doc,.docx"
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Email -------------------------------------------------------------------
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@bloodbank.local")

# --- Notification providers ----------------------------------------------------
# SMS_PROVIDER: "mock" (development only, clearly labelled) | "semaphore".
# "semaphore" is a live gateway: it needs SMS_API_KEY (never committed) and
# optionally SMS_SENDER_NAME. With no key every send fails safe.
SMS_PROVIDER = os.environ.get("SMS_PROVIDER", "mock")
SMS_API_KEY = os.environ.get("SMS_API_KEY", "")
SMS_SENDER_NAME = os.environ.get("SMS_SENDER_NAME", "BLOODBANK")
SMS_API_URL = os.environ.get("SMS_API_URL", "https://semaphore.co/api/v4/messages")
SMS_TIMEOUT_SECONDS = int(os.environ.get("SMS_TIMEOUT_SECONDS", "10"))
EMAIL_PROVIDER = os.environ.get("EMAIL_PROVIDER", "django")  # uses EMAIL_* above

# --- Security (production hardening; controlled by env) -------------------------
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if not DEBUG else None
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    X_FRAME_OPTIONS = "DENY"
else:
    X_FRAME_OPTIONS = "SAMEORIGIN"

# --- Logging -------------------------------------------------------------------
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "[{asctime}] {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
        "file": {
            "class": "logging.FileHandler",
            "filename": BASE_DIR / "logs" / "bloodbank.log",
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console", "file"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {
        "django": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
        "bloodbank": {"handlers": ["console", "file"], "level": "DEBUG", "propagate": False},
    },
}

# --- Printable documents (reports + inventory statement) ----------------------
# Shared by the print preview and the server-side PDF (core/documents.py).
# The PDF backend is xhtml2pdf (pure Python, no native pango/cairo needed).
REPORT_PAPER = os.environ.get("REPORT_PAPER", "A4")   # A4 | LETTER | LEGAL

# --- Business-rule defaults (mechanism only; values live in DB settings) ---------
# These are *software* defaults for thresholds that are operational, not clinical.
DEFAULT_EXPIRING_SOON_DAYS = int(os.environ.get("EXPIRING_SOON_DAYS", "7"))
