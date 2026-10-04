"""
Day 5: the Celery version of the hand-written worker in
ads/management/commands/run_worker.py.

What Celery now does for us — none of it is in this file:
  - the forever-loop, and pulling from Redis (blocking, so no 2-second polling)
  - running several tasks at once (--concurrency)
  - retry with a delay (self.retry below)
  - keeping a task in the queue until it finishes (CELERY_TASK_ACKS_LATE)

Still no message to the frontend when it's done — that's Day 6.
"""
import logging

from celery import shared_task

from .models import JobAd

# Same awkward import as the hand-written worker. Day 7.
from .views import call_llm, save_parsed

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


@shared_task(bind=True, max_retries=MAX_RETRIES)
def parse_ad(self, ad_id):
    """Parse one ad. Web side calls: parse_ad.delay(ad.id)"""
    try:
        ad = JobAd.objects.get(id=ad_id)
    except JobAd.DoesNotExist:
        logger.warning("ad %s: task received but not in the database, skipping", ad_id)
        return

    ad.status = "processing"
    ad.save()

    try:
        logger.info("ad %s: calling LLM (attempt %d)", ad_id, self.request.retries + 1)
        data = call_llm(ad.raw_text)
        save_parsed(ad, data)
    except Exception as exc:
        if self.request.retries < MAX_RETRIES:
            # Exponential backoff: wait 5s, then 10s, then 20s. A busy LLM gets
            # time to recover instead of being hit again immediately.
            delay = 5 * 2 ** self.request.retries
            logger.warning("ad %s: LLM call failed, retrying in %ds", ad_id, delay)
            # raise self.retry(...) puts the task back on the queue with a delay
            # and ends this attempt.
            raise self.retry(exc=exc, countdown=delay)

        # Out of retries: same rule as always — details to the log, safe message to the user.
        logger.exception("ad %s: failed after %d attempts", ad_id, MAX_RETRIES + 1)
        ad.status = "failed"
        ad.error = "Could not parse this ad. Please try again."
        ad.save()
        return

    ad.status = "completed"
    ad.save()
    logger.info("ad %s: completed", ad_id)
