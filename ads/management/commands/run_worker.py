"""
Day 5: a hand-written worker. No Celery — the bare minimum, on purpose.

    python manage.py run_worker

It runs forever, in its own process (its own container in docker compose),
separate from the web server:

    loop:
        1. pull one ad id from the Redis queue   (nothing there? sleep, ask again)
        2. call the LLM                          (the slow part, now off the web request)
        3. save the result in the database       (status -> completed / failed)

It does NOT tell the frontend anything. The page only sees the result if you
refresh it — that's Day 6's problem.

Problems this version has (feel them before fixing them):
  - Polling: with an empty queue it still asks Redis every POLL_SECONDS,
    forever. Shorter interval = more wasted asks; longer = slower pickup.
  - One at a time: 100 ads in the queue x ~1-15 s each, one after another.
  - No retry: one LLM failure = status "failed", done.
  - Crash = lost job: once RPOP takes an id, it's gone from Redis. If the
    worker dies while handling it, that ad stays "processing" forever.
  - Code changes need a restart: unlike runserver, nothing reloads this.
"""
import logging
import time

import redis
from django.conf import settings
from django.core.management.base import BaseCommand

from ads.models import JobAd

# Reusing the web code directly. A worker importing from views.py is awkward
# (views are supposed to be about HTTP) — remember that for Day 7.
from ads.views import call_llm, save_parsed

logger = logging.getLogger(__name__)

POLL_SECONDS = 2  # how long to sleep when the queue is empty


def process_ad(ad_id):
    """Steps 2 and 3 for one ad: call the LLM, save the result."""
    try:
        ad = JobAd.objects.get(id=ad_id)
    except JobAd.DoesNotExist:
        # The id was queued but the row is gone (e.g. database flushed).
        logger.warning("ad %s: in the queue but not in the database, skipping", ad_id)
        return

    ad.status = "processing"
    ad.save()

    try:
        logger.info("ad %s: calling LLM", ad_id)
        data = call_llm(ad.raw_text)  # read the text from the DB: the queue only had the id
        save_parsed(ad, data)
        ad.status = "completed"
        logger.info("ad %s: completed", ad_id)
    except Exception:
        # Same rule as before: full details to the log, a safe message to the user.
        logger.exception("ad %s: LLM call failed", ad_id)
        ad.status = "failed"
        ad.error = "Could not parse this ad. Please try again."
    ad.save()


class Command(BaseCommand):
    help = "Hand-written worker: take ad ids from Redis and parse them, forever."

    def handle(self, *args, **options):
        queue = redis.Redis.from_url(settings.REDIS_URL)
        logger.info("worker started, watching %s", settings.PARSE_QUEUE)

        while True:
            # 1. Pull. The web side LPUSHes onto the left, so RPOP from the
            #    right gives the oldest id first (FIFO). Returns None if empty.
            raw_id = queue.rpop(settings.PARSE_QUEUE)

            if raw_id is None:
                # Nothing to do. Wait, then ask again — this is the "polling".
                time.sleep(POLL_SECONDS)
                continue

            # Redis stores bytes: b"51" -> 51
            ad_id = int(raw_id)
            logger.info("ad %s: taken from the queue (%d left)", ad_id, queue.llen(settings.PARSE_QUEUE))
            process_ad(ad_id)
