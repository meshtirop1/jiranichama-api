"""One settings module, driven entirely by environment variables.

Defaults are the safe ones (DEBUG off, HTTPS redirect on) so that tests and CI run the
same way production does.
"""

from datetime import timedelta
from pathlib import Path

import environ
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
if (BASE_DIR / ".env").exists():
    environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "apps.core",
    "apps.flags",
    "apps.accounts",
    "apps.notify",
    "apps.chamas",
    "apps.ledger",
    "apps.staff",
    "apps.devices",
    "apps.rules",
    "apps.fines",
    "apps.contributions",
    "apps.feeds",
    "apps.reconciliation",
    "apps.documents",
    "apps.payouts",
    "apps.votes",
    "apps.meetings",
    "apps.loans",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {"default": env.db("DATABASE_URL")}
# PgBouncer runs in transaction pooling mode: no server-side cursors, no session state.
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=0)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("sw", "Kiswahili")]
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Private files (bank statements). Local disk in development; production moves to the
# private S3 bucket with Object Lock (6.6) behind the same storage API.
MEDIA_ROOT = env.path("MEDIA_ROOT", default=BASE_DIR / "media")
MEDIA_URL = "/never-served/"
# Nightly public anchoring (6.6): OpenTimestamps calendars (empty: not sent) and the
# outside mailbox that gets every night's root and leaves.
OTS_CALENDARS = env.list("OTS_CALENDARS", default=[
    "https://a.pool.opentimestamps.org", "https://b.pool.opentimestamps.org"])
ANCHOR_EMAIL_TO = env.list("ANCHOR_EMAIL_TO", default=[])
# SMS commands from basic phones (4.9): the secret token in the provider's incoming-SMS
# callback URL (empty: the hook is off), its IPs, and how many messages a number may send
# an hour.
SMS_INBOUND_TOKEN = env("SMS_INBOUND_TOKEN", default="")
SMS_INBOUND_ALLOWED_IPS = env.list("SMS_INBOUND_ALLOWED_IPS", default=[])
SMS_COMMANDS_PER_HOUR = env.int("SMS_COMMANDS_PER_HOUR", default=10)
# Payment receipts go by SMS to members who have not opened the app in this many days.
RECEIPT_SMS_IF_AWAY_DAYS = env.int("RECEIPT_SMS_IF_AWAY_DAYS", default=30)
# M-Pesa Daraja C2B (6.9). Our public base URL for Safaricom's callbacks, the platform's
# Daraja app credentials (used only to register callback URLs), and Safaricom's IPs.
DARAJA_BASE_URL = env("DARAJA_BASE_URL", default="https://sandbox.safaricom.co.ke")
DARAJA_CONSUMER_KEY = env("DARAJA_CONSUMER_KEY", default="")
DARAJA_CONSUMER_SECRET = env("DARAJA_CONSUMER_SECRET", default="")
DARAJA_CALLBACK_BASE_URL = env("DARAJA_CALLBACK_BASE_URL",
                               default="https://api.jiranichama.co.ke")
DARAJA_ALLOWED_IPS = env.list("DARAJA_ALLOWED_IPS", default=[])
# Device keys (6.7). SHA-256 fingerprints of the attestation roots we trust (Google's
# hardware attestation roots in production). Until set, keys are recorded as untrusted;
# production turns on DEVICE_REQUIRE_TRUSTED_ATTESTATION once the roots are in.
ANDROID_ATTESTATION_ROOTS = env.list("ANDROID_ATTESTATION_ROOTS", default=[])
DEVICE_REQUIRE_TRUSTED_ATTESTATION = env.bool("DEVICE_REQUIRE_TRUSTED_ATTESTATION",
                                              default=False)
# Printed (and in the QR code) on every statement PDF; the page is served by this API.
VERIFY_BASE_URL = env("VERIFY_BASE_URL", default="https://api.jiranichama.co.ke/verify/")

# --- Security -------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_HSTS_SECONDS = env.int("DJANGO_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_AGE = 60 * 60 * 8  # staff console sessions: one working day
SILENCED_SYSTEM_CHECKS = env.list("DJANGO_SILENCED_CHECKS", default=[])

# --- API ------------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.accounts.authentication.AppJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.core.problems.problem_exception_handler",
    # Per-IP limits on sign-in codes. Raised only for local end-to-end runs, where every
    # test user signs in from 127.0.0.1.
    "DEFAULT_THROTTLE_RATES": {
        "otp": env("OTP_THROTTLE_RATE", default="20/hour"),
        "otp_verify": env("OTP_VERIFY_THROTTLE_RATE", default="60/hour"),
        # The public statement check page (no sign-in).
        "verify": env("VERIFY_THROTTLE_RATE", default="30/minute"),
    },
    "UNAUTHENTICATED_USER": None,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": False,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "JiraniChama API",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "OAS_VERSION": "3.1.0",
    "ENUM_NAME_OVERRIDES": {
        "ProposalKindEnum": "apps.payouts.models.Proposal.Kind",
        "DocumentKindEnum": "apps.documents.models.IssuedDocument.Kind",
    },
}

# --- Celery ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6390/0")
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_DEFAULT_QUEUE = "bulk"
CELERY_TASK_ROUTES = {
    "apps.feeds.*": {"queue": "feeds"},
    "apps.rules.*": {"queue": "rules"},
    "apps.notify.*": {"queue": "notify"},
    "apps.documents.*": {"queue": "documents"},
    "apps.ledger.tasks.sign_*": {"queue": "signing"},
    "apps.core.tasks.publish_outbox": {"queue": "notify"},
}
CELERY_BEAT_SCHEDULE = {
    "publish-outbox": {"task": "apps.core.tasks.publish_outbox", "schedule": 30.0},
    "sign-pending-entries": {"task": "apps.ledger.tasks.sign_pending", "schedule": 60.0},
    "raise-late-fines": {"task": "apps.fines.tasks.raise_due_fines",
                         "schedule": crontab(minute=5)},
    "reconcile-daily": {"task": "apps.reconciliation.tasks.reconcile_daily",
                        "schedule": crontab(hour=6, minute=0)},
    "statement-reminders": {"task": "apps.reconciliation.tasks.remind_overdue_statements",
                            "schedule": crontab(hour=7, minute=0)},
    "close-votes": {"task": "apps.votes.tasks.close_due_votes", "schedule": crontab(minute=10)},
    "rule-proposals": {"task": "apps.rules.tasks.advance_rule_proposals",
                       "schedule": crontab(minute=20)},
    "meeting-reminders": {"task": "apps.meetings.tasks.meeting_reminders",
                          "schedule": crontab(minute="*/10")},
    "seal-minutes": {"task": "apps.meetings.tasks.seal_minutes", "schedule": crontab(minute=30)},
    "expire-waivers": {"task": "apps.fines.tasks.expire_waivers", "schedule": crontab(minute=40)},
    "loans-daily": {"task": "apps.loans.tasks.loans_daily",
                    "schedule": crontab(hour=7, minute=30)},
    "expire-proposals": {"task": "apps.payouts.tasks.expire_proposals",
                         "schedule": crontab(minute=15)},
    "expire-stale-setups": {"task": "apps.chamas.tasks.expire_setups",
                            "schedule": crontab(hour=2, minute=0)},
    "anchor-nightly": {"task": "apps.ledger.tasks.anchor_nightly",
                       "schedule": crontab(hour=0, minute=30)},
    "verify-chains-nightly": {
        "task": "apps.ledger.tasks.verify_chains",
        "schedule": crontab(hour=1, minute=0),
    },
}

# --- JiraniChama ----------------------------------------------------------------------
FIELD_ENCRYPTION_KEYS = env.list("FIELD_ENCRYPTION_KEYS")
HMAC_KEY = env("HMAC_KEY")

PLATFORM_SIGNER = env("PLATFORM_SIGNER", default="local")
PLATFORM_SIGNING_KEY_PEM = env("PLATFORM_SIGNING_KEY_PEM", default="").replace("\\n", "\n")
KMS_KEY_ID = env("KMS_KEY_ID", default="")
AWS_REGION = env("AWS_REGION", default="")

SMS_BACKENDS = env.list("SMS_BACKENDS", default=["apps.notify.sms.ConsoleBackend"])
AFRICASTALKING_USERNAME = env("AFRICASTALKING_USERNAME", default="sandbox")
AFRICASTALKING_API_KEY = env("AFRICASTALKING_API_KEY", default="")
AFRICASTALKING_SENDER_ID = env("AFRICASTALKING_SENDER_ID", default="")
AFRICASTALKING_SANDBOX = env.bool("AFRICASTALKING_SANDBOX", default=True)
# Local development only (see apps.notify.sms.DevFileBackend). Ignored unless DEBUG is on.
DEV_SMS_FILE = env("DEV_SMS_FILE", default="") if DEBUG else ""

# Statements
STATEMENT_MAX_BYTES = env.int("STATEMENT_MAX_BYTES", default=5 * 1024 * 1024)
STATEMENT_INBOX_DOMAIN = env("STATEMENT_INBOX_DOMAIN", default="statements.jiranichama.co.ke")
MAIL_HOOK_TOKEN = env("MAIL_HOOK_TOKEN", default="")
MAIL_HOOK_ALLOWED_IPS = env.list("MAIL_HOOK_ALLOWED_IPS", default=[])
# Bank sending domains for e-statements, e.g. {"equity": ["equitybank.co.ke"]}.
# Empty until each bank's real sending domain is confirmed: until then every statement
# email is held for staff instead of accepted.
BANK_STATEMENT_SENDERS = env.json("BANK_STATEMENT_SENDERS", default={})

OTP_TTL_SECONDS = 300
OTP_MAX_ATTEMPTS = 5
# Codes per phone per window. Raised only for local end-to-end runs.
OTP_MAX_PER_WINDOW = env.int("OTP_MAX_PER_WINDOW", default=3)
OTP_WINDOW_SECONDS = 600

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}
