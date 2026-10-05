"""
The queue's front desk — the worker-side twin of views.py.

views.py turns an HTTP request into a call to services.py.
tasks.py turns a queue message (an ad id) into calls to services.py.

What's here is only what's specific to Celery: the task itself, and the
retry decision (how many times, how long to wait). The actual work —
marking statuses, calling the LLM, saving — lives in services.py.

What Celery does for us — none of it is in this file:
  - the forever-loop, and pulling from Redis (blocking, so no 2-second polling)
  - running several tasks at once (--concurrency)
  - retry with a delay (self.retry below)
  - keeping a task in the queue until it finishes (CELERY_TASK_ACKS_LATE)
"""
import logging

from celery import shared_task

from . import services

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


@shared_task(bind=True, max_retries=MAX_RETRIES)
def parse_ad(self, ad_id):
    """Parse one ad. Queued by services.enqueue_parse(ad_id)."""
    ad = services.start_processing(ad_id)
    if ad is None:
        return

    try:
        logger.info("ad %s: calling LLM (attempt %d)", ad_id, self.request.retries + 1)
        services.parse_and_save(ad)
    except Exception as exc:
        if self.request.retries < MAX_RETRIES:
            # Exponential backoff: wait 5s, then 10s, then 20s. A busy LLM gets
            # time to recover instead of being hit again immediately.
            delay = 5 * 2 ** self.request.retries
            logger.warning("ad %s: LLM call failed, retrying in %ds", ad_id, delay)
            # raise self.retry(...) puts the task back on the queue with a delay
            # and ends this attempt.
            raise self.retry(exc=exc, countdown=delay)

        # Out of retries: full details to the log, a safe message to the user.
        logger.exception("ad %s: failed after %d attempts", ad_id, MAX_RETRIES + 1)
        services.mark_failed(ad)
        return

    services.mark_completed(ad)
