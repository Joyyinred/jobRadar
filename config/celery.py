"""
The Celery app. Boilerplate from the Celery docs for Django projects.

Start a worker with:
    celery -A config worker --loglevel=info
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("jobradar")

# Read every setting that starts with CELERY_ from settings.py
# (CELERY_BROKER_URL -> broker_url, and so on).
app.config_from_object("django.conf:settings", namespace="CELERY")

# Find tasks.py in every installed app (ads/tasks.py).
app.autodiscover_tasks()


# ---------------------------------------------------------------------------
# Day 11: expose the worker's metrics for Prometheus on :9100.
#
# The worker isn't a web server, so it gets its own tiny HTTP endpoint.
# worker_ready fires only in the worker, never in the web process (which
# imports this module too).
#
# The worker must run with --pool=threads (docker-compose.yml): with the
# default prefork pool every child process keeps its OWN counters, and this
# endpoint — in the parent — would only ever show zeros.
# ---------------------------------------------------------------------------
from celery.signals import worker_ready  # noqa: E402
from prometheus_client import start_http_server  # noqa: E402

WORKER_METRICS_PORT = 9100


@worker_ready.connect
def start_metrics_server(**kwargs):
    start_http_server(WORKER_METRICS_PORT)
