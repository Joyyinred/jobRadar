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
    "django.contrib.auth",          # contenttypes depends on it
    "django.contrib.staticfiles",
    "rest_framework",
    "ads",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
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

# No authentication yet (single local user, see DESIGN_DOC §3.2), so DRF's
# browsable API and JSON renderer are enough. Session/CSRF come with real auth.
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
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
