"""
Business logic: what JobRadar actually does, with no HTTP in sight.

Called by two kinds of "front desk":
  - views.py  (HTTP requests from the browser)
  - tasks.py  (messages from the Redis queue, in the Celery worker)

Rule of thumb: nothing in here knows about request, Response or status codes,
so the same function works from a view, a worker, a script or a test.
"""
import logging

from .llm import call_llm
from .models import AdSkill, Company, JobAd, Skill

# __name__ is "ads.services", so it inherits the "ads" logger from settings.LOGGING.
logger = logging.getLogger(__name__)


# --- Reading ----------------------------------------------------------------

def list_ads():
    """Every ad, newest first."""
    # select_related("company") fetches each ad's company in the SAME query
    # (a SQL JOIN). Without it, ad.company.name would run one extra query per
    # ad: 100 ads = 101 queries. That's called the "N+1 problem".
    # No pagination: 1,000 ads = 1,000 rows. Fine for now.
    return JobAd.objects.select_related("company").order_by("-created_at")


def get_ad(ad_id):
    """One ad. Raises JobAd.DoesNotExist if there is no such id."""
    return JobAd.objects.get(id=ad_id)


# --- Submitting and retrying (web side) --------------------------------------

def submit_ad(raw_text):
    """Save a new ad as pending and queue it for parsing. Returns the JobAd."""
    # 1. Save the raw ad, status pending. The database is the source of truth.
    ad = JobAd.objects.create(raw_text=raw_text, status="pending")
    logger.info("ad %s saved (%d chars)", ad.id, len(raw_text))  # ids only, never ad text

    # 2. Hand ONLY the id to the queue — the ad itself is already in the database.
    enqueue_parse(ad.id)
    logger.info("ad %s queued", ad.id)
    return ad


def retry_ad(ad_id):
    """Re-queue an ad whose parse failed.

    Returns the JobAd (now pending) if it was re-queued, or None if it wasn't
    "failed" or doesn't exist — the caller works out which.
    """
    # Check and change in ONE database step. Only a row that is still "failed"
    # gets updated; .update() returns how many rows it changed (0 or 1).
    # Two quick clicks: the first changes 1 row, the second finds the status
    # already "pending", changes 0 rows. The ad is queued once, not twice.
    updated = JobAd.objects.filter(id=ad_id, status="failed").update(status="pending", error="")
    if not updated:
        return None

    # Read it BEFORE queueing: once queued, a worker may flip it to
    # "processing" at any moment.
    ad = JobAd.objects.get(id=ad_id)
    enqueue_parse(ad_id)
    logger.info("ad %s: retry queued", ad_id)
    return ad


def enqueue_parse(ad_id):
    """Put one ad id on the parse queue.

    .delay() doesn't run the task here: it puts a message on the Redis queue
    and returns at once. A worker process picks it up.
    """
    # Imported inside the function on purpose. The task needs this module (to
    # do the work) and this module needs the task (to queue it): two files
    # importing each other at the top fails. This is the only place that
    # import happens, so the cycle is contained in one line.
    from .tasks import parse_ad
    parse_ad.delay(ad_id)


# --- Parsing (worker side) ---------------------------------------------------

def start_processing(ad_id):
    """Mark an ad processing and return it, or None if it no longer exists."""
    try:
        ad = JobAd.objects.get(id=ad_id)
    except JobAd.DoesNotExist:
        logger.warning("ad %s: task received but not in the database, skipping", ad_id)
        return None
    ad.status = "processing"
    ad.save()
    return ad


def parse_and_save(ad):
    """Call the LLM on the ad's raw text and store the result. Raises on failure."""
    data = call_llm(ad.raw_text)
    save_parsed(ad, data)


def mark_completed(ad):
    ad.status = "completed"
    ad.save()
    logger.info("ad %s: completed", ad.id)


def mark_failed(ad):
    # Details go to the log (the caller logs the exception); the user only
    # gets a safe message.
    ad.status = "failed"
    ad.error = "Could not parse this ad. Please try again."
    ad.save()


def save_parsed(ad, data):
    """Split the LLM's one JSON object across the tables.

    data looks like {"title": ..., "company": ..., "required_skills": [...], ...}
    """
    # Company: reuse the row if this exact name exists, otherwise create it.
    # get_or_create returns (object, created?) — we only need the object.
    company_name = (data.get("company") or "").strip()
    if company_name and company_name != "unspecified":
        ad.company, _ = Company.objects.get_or_create(name=company_name)

    # Per-ad fields: plain columns on JobAd.
    ad.title = data.get("title") or ""
    ad.city = data.get("city") or ""
    ad.seniority = data.get("seniority") or ""
    ad.swedish_requirement = data.get("swedish_requirement") or ""
    ad.save()

    # Skills: one Skill row per name (shared by all ads), plus one AdSkill row
    # per (this ad, that skill) saying how much the ad wants it.
    # Start clean: a retry (or a half-finished earlier attempt) must not mix
    # old skill links with new ones. The Skill rows themselves stay.
    ad.ad_skills.all().delete()
    for level, key in [("required", "required_skills"), ("nice_to_have", "nice_to_have_skills")]:
        for name in data.get(key) or []:
            skill, _ = Skill.objects.get_or_create(name=name.strip())
            # get_or_create again: if the LLM lists a skill twice, the
            # unique (ad, skill) constraint would otherwise reject the 2nd row.
            AdSkill.objects.get_or_create(ad=ad, skill=skill, defaults={"level": level})
