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
