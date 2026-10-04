# Load the Celery app whenever Django starts, so that `parse_ad.delay(...)`
# in the web process knows where to send tasks.
from .celery import app as celery_app

__all__ = ("celery_app",)
