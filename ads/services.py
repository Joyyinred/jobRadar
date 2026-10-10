"""
Business logic: what JobRadar actually does, with no HTTP in sight.

Called by two kinds of "front desk":
  - views.py  (HTTP requests from the browser)
  - tasks.py  (messages from the Redis queue, in the Celery worker)

Rule of thumb: nothing in here knows about request, Response or status codes,
so the same function works from a view, a worker, a script or a test.
"""
import hashlib
import logging
import re

from django.db import IntegrityError, transaction
from django.utils import timezone

from .adapters import PasteAdapter
from .exceptions import BlockError, NotFoundError, ValidationError, WarningException
from . import metrics
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
    """One ad. Raises NotFoundError (-> 404) if there is no such id."""
    try:
        return JobAd.objects.get(id=ad_id)
    except JobAd.DoesNotExist:
        raise NotFoundError("AD_NOT_FOUND", f"Ad {ad_id} not found", {"id": ad_id})


# --- Ingesting ads (web side) -------------------------------------------------
#
# Every source goes through ingest(). Adapters (adapters.py) only translate a
# source into RawJobAd; the rules about an ad live here, once.

MAX_AD_CHARS = 50_000   # longer is rejected, not cut: the cut part could be the requirements
SHORT_AD_CHARS = 200    # below this it's probably not a whole ad


def normalise(raw_text):
    """Same ad, different whitespace -> same text. Used only for the hash;
    raw_text itself is stored untouched."""
    return re.sub(r"\s+", " ", raw_text.replace("\r\n", "\n")).strip()


def content_hash_of(raw_text):
    """SHA-256 of the normalised text: a 64-character fingerprint of the ad."""
    return hashlib.sha256(normalise(raw_text).encode("utf-8")).hexdigest()


def ingest(raw, confirm=False):
    """_ingest() plus counting the outcome for monitoring (Day 11).

    Counting lives in this thin wrapper so the rules in _ingest() don't need
    a metrics line before every return and raise.
    """
    try:
        ad, created = _ingest(raw, confirm)
    except WarningException:
        metrics.INGEST.labels(raw.source, "warning").inc()
        raise
    except ValidationError:
        metrics.INGEST.labels(raw.source, "invalid").inc()
        raise
    metrics.INGEST.labels(raw.source, "created" if created else "duplicate").inc()
    return ad, created


def _ingest(raw, confirm=False):
    """Store one RawJobAd as a pending ad and queue it for parsing.

    The ONLY place a JobAd is created from outside data — whichever adapter
    produced `raw`, the same rules apply.

    Returns (ad, created):
      (new JobAd, True)       — saved and queued
      (existing JobAd, False) — already have it (same source id, or same text).
                                Not an error: the ad IS in the system (idempotent).
    Raises:
      ValidationError  — the ad itself is unusable (empty / too long)
      WarningException — allowed only with confirm=True (nothing saved):
                           URL_ALREADY_USED  same source_url, different text
                           AD_TOO_SHORT      under SHORT_AD_CHARS
    """
    # 1. Rules about the ad itself. These live here, not only in the serializer:
    #    a JobTech import never passes through a serializer.
    if not raw.raw_text.strip():
        raise ValidationError("AD_EMPTY", "The ad is empty.")
    if len(raw.raw_text) > MAX_AD_CHARS:
        raise ValidationError(
            "AD_TOO_LONG", f"The ad is over {MAX_AD_CHARS:,} characters.",
            {"chars": len(raw.raw_text), "max_chars": MAX_AD_CHARS},
        )

    # 2. Already imported from this source? (Re-running an import must not duplicate.)
    if raw.source_external_id:
        existing = find_by_external_id(raw.source, raw.source_external_id)
        if existing:
            return existing, False

    # 3. Exact duplicate, same text -> hand back the ad we already have.
    #    No new row, no second LLM call.
    content_hash = content_hash_of(raw.raw_text)
    existing = JobAd.objects.filter(content_hash=content_hash).first()
    if existing:
        logger.info("ad %s: same content submitted again", existing.id)
        return existing, False

    # 4. Same URL but DIFFERENT text: could be the same ad edited, or a second
    #    role on one careers page. Don't silently drop it, and don't silently
    #    store it twice either — ask. Confirmed -> stored as a new ad.
    if raw.source_url and not confirm:
        existing = JobAd.objects.filter(source_url=raw.source_url).first()
        if existing:
            raise WarningException(
                "URL_ALREADY_USED",
                f"Ad #{existing.id} came from this link but has different text. Save this one as a new ad?",
                {"existing_id": existing.id, "source_url": raw.source_url},
            )

    # 5. Warning: allowed, but only after the user confirms.
    length = len(normalise(raw.raw_text))
    if length < SHORT_AD_CHARS and not confirm:
        raise WarningException(
            "AD_TOO_SHORT",
            f"This is under {SHORT_AD_CHARS} characters — it may not be a whole ad. Parse it anyway?",
            {"chars": length, "min_chars": SHORT_AD_CHARS},
        )

    # 6. Save the raw ad, status pending. The database is the source of truth.
    try:
        # atomic: if the INSERT fails, roll back just this step cleanly.
        with transaction.atomic():
            ad = JobAd.objects.create(
                raw_text=raw.raw_text,
                content_hash=content_hash,
                source=raw.source,
                source_url=raw.source_url,
                source_external_id=raw.source_external_id,
                expires_at=raw.expires_at,
                status="pending",
            )
    except IntegrityError:
        # The second line of defence. Two identical submits at the same moment
        # both passed the checks above; a unique constraint (content_hash, or
        # source + external id) let only one INSERT through. This one lost —
        # return the winner.
        existing = JobAd.objects.filter(content_hash=content_hash).first()
        if existing is None and raw.source_external_id:
            existing = find_by_external_id(raw.source, raw.source_external_id)
        if existing is None:
            raise  # some other constraint failed — not a duplicate, don't hide it
        logger.info("ad %s: duplicate caught by the database constraint", existing.id)
        return existing, False
    logger.info("ad %s saved from %s (%d chars)", ad.id, raw.source, len(raw.raw_text))  # ids only, never ad text

    # 7. Hand ONLY the id to the queue — the ad itself is already in the database.
    enqueue_parse(ad.id)
    logger.info("ad %s queued", ad.id)
    return ad, True


def find_by_external_id(source, external_id):
    return JobAd.objects.filter(source=source, source_external_id=external_id).first()


def ingest_one(adapter, confirm=False):
    """Run a single-ad adapter (paste, one Platsbanken link) through ingest().

    Returns (ad, created), like ingest().
    """
    # Known id before fetching (e.g. read from the link)? If we already have
    # that ad, skip the network call — this also works after the ad has been
    # taken down at the source, when fetching it would be a 404.
    if adapter.external_id:
        existing = find_by_external_id(adapter.source, adapter.external_id)
        if existing:
            metrics.INGEST.labels(adapter.source, "duplicate").inc()
            return existing, False

    [raw] = adapter.fetch()
    return ingest(raw, confirm=confirm)


def ingest_batch(adapter):
    """Run a many-ads adapter (a JobTech search) through ingest().

    One bad ad must not sink the batch: ads that fail a rule are skipped and
    counted. There's no user to confirm warnings in a batch, so warned ads are
    skipped too. Returns counts, e.g. {"created": 8, "duplicates": 2, "skipped": 0}.
    """
    counts = {"created": 0, "duplicates": 0, "skipped": 0}
    for raw in adapter.fetch():
        try:
            _, created = ingest(raw)
        except (ValidationError, WarningException) as error:
            logger.info("skipped %s ad %s: %s", raw.source, raw.source_external_id, error.code)
            counts["skipped"] += 1
            continue
        counts["created" if created else "duplicates"] += 1
    logger.info("batch %s: %s", type(adapter).__name__, counts)
    return counts


def submit_ad(raw_text, source_url=None, confirm=False):
    """Pasted text from the form — the paste adapter run through ingest()."""
    return ingest_one(PasteAdapter(raw_text, source_url), confirm=confirm)


def retry_ad(ad_id):
    """Re-queue an ad whose parse failed.

    Returns the JobAd (now pending). Raises NotFoundError (404) or
    BlockError (409) if it can't be retried.
    """
    # Check and change in ONE database step. Only a row that is still "failed"
    # gets updated; .update() returns how many rows it changed (0 or 1).
    # Two quick clicks: the first changes 1 row, the second finds the status
    # already "pending", changes 0 rows. The ad is queued once, not twice.
    updated = JobAd.objects.filter(id=ad_id, status="failed").update(status="pending", error="")
    if not updated:
        current = get_ad(ad_id)  # raises NotFoundError if it doesn't exist
        raise BlockError(
            "AD_NOT_RETRYABLE",
            f"Ad {ad_id} is {current.status}; only failed ads can be retried",
            {"id": ad_id, "status": current.status},
        )

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
    # Saved -> parsed, including time in the queue and any retries: what the
    # user actually waited, not just the LLM call.
    metrics.AD_PARSE_SECONDS.observe((timezone.now() - ad.created_at).total_seconds())
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
