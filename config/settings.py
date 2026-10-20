"""
Django settings for JobRadar.

Carried over from the CarePlan project, with three changes:
  - Django REST Framework is installed (the API returns JSON, not HTML pages)
  - the app is "ads", and logging is configured for it
  - SECRET_KEY is read from the environment, with a dev-only fallback
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Dev fallback only. Any real deployment must set DJANGO_SECRET_KEY.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-a-real-secret")

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",  # required by the ORM's migration machinery
    "django.contrib.auth",          # users and password hashing
    "django.contrib.sessions",      # login sessions, stored in the database
    "django.contrib.staticfiles",
    "rest_framework",
    "django_prometheus",            # Day 11: /metrics + automatic HTTP metrics
    "ads",
    "tracking",                     # your job applications (per user; uses ads, never the reverse)
]

MIDDLEWARE = [
    # Day 11: Before/After wrap everything else, so the timing covers the whole
    # request. They record request counts, latency and status codes per view.
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",  # reads the sessionid cookie
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",  # session -> request.user
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "jobradar"),
        "USER": os.environ.get("POSTGRES_USER", "jobradar"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "jobradar"),
        # "db" inside docker compose, "localhost" when running from a venv.
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        # 5434 from the host (see docker-compose.yml); compose overrides to 5432.
        "PORT": os.environ.get("POSTGRES_PORT", "5434"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Stockholm"  # same-day rules use the Stockholm calendar date
USE_I18N = True
USE_TZ = True                   # store UTC, display in TIME_ZONE

STATIC_URL = "static/"

# The session cookie: HttpOnly (JavaScript can't read it, so an XSS bug can't
# steal a login) and SameSite=Lax (other sites' forms don't send it along).
# Both are Django's defaults; written out because they are the point.
# Secure (HTTPS only) is switched on with DEBUG off, i.e. when deployed.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_AGE = 14 * 24 * 60 * 60  # two weeks, then log in again
# The page reads the csrftoken cookie to send it back as X-CSRFToken, so this
# one cookie must NOT be HttpOnly (it isn't by default). It's no secret on
# its own: other sites can't read our cookies, which is what makes it work.
# Vite (:5173) forwards /api to Django; Django must trust that origin for CSRF.
CSRF_TRUSTED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

# Login: Django sessions (see ads/auth.py for why not JWT).
#   Anyone may READ (browse ads, market stats). Anything that writes or costs
#   money (adding ads, imports, retries -> LLM calls) needs a logged-in user.
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": ["ads.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticatedOrReadOnly"],
    # Guessing passwords: at most 5 login attempts a minute per IP address.
    "DEFAULT_THROTTLE_RATES": {"login": "5/min"},
    # Day 8: every error, ours or DRF's, leaves through this one function in
    # one JSON shape: {"type", "code", "message", "detail"}.
    "EXCEPTION_HANDLER": "ads.exceptions.exception_handler",
}

# ---------------------------------------------------------------------------
# Logging
#
# Without this block logger.info() produces nothing: Python's default root
# level is WARNING. Never log raw ad text or CV text — ids only.
# ---------------------------------------------------------------------------
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)  # FileHandler creates the file, never the folder

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} {levelname:<7} {name} | {message}",
            "datefmt": "%H:%M:%S",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOGS_DIR / "jobradar.log",
            "maxBytes": 1024 * 1024,
            "backupCount": 3,
            "encoding": "utf-8",  # required on Windows for å ä ö
            "formatter": "verbose",
        },
    },
    "loggers": {
        "ads": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

# ---------------------------------------------------------------------------
# Redis (Day 4: the parse queue)
#
# "redis" inside docker compose (set in docker-compose.yml), "localhost" when
# running Django from the .venv for debugging.
# ---------------------------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
PARSE_QUEUE = "jobradar:parse_queue"  # the Redis list that holds ad ids

# ---------------------------------------------------------------------------
# Celery (Day 5): the worker framework, using Redis as its queue ("broker").
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = REDIS_URL
# Remove a task from the queue only AFTER it finishes, not when it's picked up.
# If the worker dies mid-task, Redis hands the task out again (after a timeout)
# — the "crash = lost job" problem of the hand-written worker.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
