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
from django.db.models import Count, Max
from django.utils import timezone

from .adapters import DATA_IT_FIELD, JobTechSearchAdapter, PasteAdapter
from .exceptions import BlockError, NotFoundError, UpstreamError, ValidationError, WarningException
from . import metrics
from .llm import call_llm
from .models import AdSkill, Company, ImportJob, JobAd, Skill
from .occupation_labels import english_label
from .skill_aliases import canonical_names, case_matters

# __name__ is "ads.services", so it inherits the "ads" logger from settings.LOGGING.
logger = logging.getLogger(__name__)


# --- Reading ----------------------------------------------------------------

def list_ads(seniority=None, employment_type=None, occupation_group=None, origin=None, degree_level=None):
    """Ads, newest first, optionally filtered. A query, not rows yet: the view
    pages it (pagination.py), so only one page is ever read.

    seniority:        list, e.g. ["junior", "intern"]
    employment_type:  e.g. "thesis"
    occupation_group: a JobTech group code, e.g. "DJh5_yyF_hEM"
    origin:           "user" (added by you) or "import"
    degree_level:     "master" or "bachelor" — also matches ads open to
                      "either": a master student looking for a thesis wants those too
    """
    # select_related("company") fetches each ad's company in the SAME query
    # (a SQL JOIN). Without it, ad.company.name would run one extra query per
    # ad: 100 ads = 101 queries. That's called the "N+1 problem".
    # "-id" breaks ties: ads imported in the same instant still have one fixed
    # order, so no ad shows up on two pages or on none.
    ads = JobAd.objects.select_related("company").order_by("-created_at", "-id")
    if seniority:
        ads = ads.filter(seniority__in=seniority)
    if employment_type:
        ads = ads.filter(employment_type=employment_type)
    if occupation_group:
        ads = ads.filter(occupation_group_code=occupation_group)
    if origin:
        ads = ads.filter(origin=origin)
    if degree_level:
        ads = ads.filter(degree_level__in=[degree_level, "either"])
    return ads


def occupation_groups():
    """JobTech occupation groups present in the data, most ads first:
    [{"code": "DJh5_yyF_hEM", "label": "Software and systems developers", "ads": 60}, ...]
    For the filter drop-downs. Pasted ads (no group) aren't listed.
    """
    # Group by the CODE only. The label belongs to the code, but JobTech
    # sometimes sends a code without a label — grouping by (code, label) split
    # one group in two (82 + 2) and the drop-down disagreed with the stats.
    # Max() picks the non-empty label ("" sorts before any text).
    rows = (
        JobAd.objects.exclude(occupation_group_code="")
        .values("occupation_group_code")
        .annotate(ads=Count("id"), label=Max("occupation_group"))
    )
    groups = [
        {"code": r["occupation_group_code"], "label": english_label(r["occupation_group_code"], r["label"]), "ads": r["ads"]}
        for r in rows
    ]
    # Sorted here, not in SQL: ties go alphabetically by the name people see.
    return sorted(groups, key=lambda g: (-g["ads"], g["label"]))


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


def ingest(raw, confirm=False, origin="user"):
    """_ingest() plus counting the outcome for monitoring (Day 11).

    Counting lives in this thin wrapper so the rules in _ingest() don't need
    a metrics line before every return and raise.
    """
    try:
        ad, created = _ingest(raw, confirm, origin)
    except WarningException:
        metrics.INGEST.labels(raw.source, "warning").inc()
        raise
    except ValidationError:
        metrics.INGEST.labels(raw.source, "invalid").inc()
        raise
    metrics.INGEST.labels(raw.source, "created" if created else "duplicate").inc()
    return ad, created


def _ingest(raw, confirm=False, origin="user"):
    """Store one RawJobAd as a pending ad and queue it for parsing.

    origin: "user" (Add ads page) or "import" (a bulk import) — see JobAd.origin.

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
                occupation_group_code=raw.occupation_group_code,
                occupation_group=raw.occupation_group,
                apply_url=raw.apply_url,
                apply_email=raw.apply_email,
                origin=origin,
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
            return claim(existing), False

    [raw] = adapter.fetch()
    ad, created = ingest(raw, confirm=confirm)
    return (ad if created else claim(ad)), created


def claim(ad):
    """You added an ad yourself that a bulk import had already brought in:
    it's yours now — that you chose it is what "user" means."""
    if ad.origin != "user":
        ad.origin = "user"
        ad.save(update_fields=["origin"])
    return ad


def ingest_batch(adapter, on_page=None):
    """Run a many-ads adapter (a JobTech search) through ingest(), page by page.

    One bad ad must not sink the batch: ads that fail a rule are skipped and
    counted. There's no user to confirm warnings in a batch, so warned ads are
    skipped too. Returns counts, e.g. {"created": 8, "duplicates": 2, "skipped": 0}.

    on_page(fetched, counts), if given, is called after every page — that's
    how a background import reports its progress.
    """
    counts = {"created": 0, "duplicates": 0, "skipped": 0}
    fetched = 0
    for page in adapter.pages():
        fetched += len(page)
        for raw in page:
            try:
                _, created = ingest(raw, origin="import")
            except (ValidationError, WarningException) as error:
                logger.info("skipped %s ad %s: %s", raw.source, raw.source_external_id, error.code)
                counts["skipped"] += 1
                continue
            counts["created" if created else "duplicates"] += 1
        if on_page:
            on_page(fetched, counts)
    logger.info("batch %s: %s", type(adapter).__name__, counts)
    return counts


# --- Bulk imports in the background -------------------------------------------
#
# A JobTech search import can take a minute (pages of results, each a network
# call), too long for a web request — the same lesson as parsing on Day 4. So
# the web side only records an ImportJob and queues it; a worker runs it.

def start_jobtech_import(params):
    """Record an import and queue it. Returns the ImportJob (status pending).

    params: the validated request body — {"query", "limit", "data_it_only"}.
    """
    job = ImportJob.objects.create(params=params)
    enqueue_import(job.id)
    logger.info("import %s queued: %s", job.id, params)
    return job


def get_import(job_id):
    """One ImportJob. Raises NotFoundError (-> 404) if there is no such id."""
    try:
        return ImportJob.objects.get(id=job_id)
    except ImportJob.DoesNotExist:
        raise NotFoundError("IMPORT_NOT_FOUND", f"Import {job_id} not found", {"id": job_id})


def run_jobtech_import(job_id):
    """The worker side: run one ImportJob, saving progress after every page."""
    try:
        job = ImportJob.objects.get(id=job_id)
    except ImportJob.DoesNotExist:
        logger.warning("import %s: task received but not in the database, skipping", job_id)
        return
    job.status = "running"
    job.save(update_fields=["status"])

    params = job.params
    adapter = JobTechSearchAdapter(
        params.get("query", ""), params["limit"],
        occupation_field=DATA_IT_FIELD if params.get("data_it_only") else None,
    )

    def save_progress(fetched, counts):
        job.fetched = fetched
        job.created, job.duplicates, job.skipped = counts["created"], counts["duplicates"], counts["skipped"]
        job.save(update_fields=["fetched", "created", "duplicates", "skipped"])

    try:
        ingest_batch(adapter, on_page=save_progress)
        job.status = "completed"
    except UpstreamError as error:
        # JobTech down or erroring mid-way. Whatever was imported stays (and
        # re-running is safe: ingest() is idempotent).
        logger.warning("import %s: JobTech failed: %s", job_id, error.code)
        job.status, job.error = "failed", error.message
    except Exception:
        # A bug. Without this, the job would stay "running" forever.
        logger.exception("import %s: unexpected error", job_id)
        job.status, job.error = "failed", "The import failed unexpectedly."
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error", "finished_at"])
    logger.info("import %s %s: fetched %d, created %d", job_id, job.status, job.fetched, job.created)


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


def enqueue_import(job_id):
    """Put one ImportJob id on the queue (same pattern as enqueue_parse)."""
    from .tasks import run_import
    run_import.delay(job_id)


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
    # No title_en (an older prompt, or the model left it out): the original
    # title is the best English title we have, and the list is never blank.
    ad.title_en = data.get("title_en") or ad.title
    ad.city = data.get("city") or ""
    ad.seniority = data.get("seniority") or ""
    ad.employment_type = data.get("employment_type") or ""
    ad.degree_level = data.get("degree_level") or ""
    ad.swedish_requirement = data.get("swedish_requirement") or ""
    ad.save()

    # Skills: one Skill row per name (shared by all ads), plus one AdSkill row
    # per (this ad, that skill) saying how much the ad wants it.
    # Start clean: a retry (or a half-finished earlier attempt) must not mix
    # old skill links with new ones. The Skill rows themselves stay.
    ad.ad_skills.all().delete()

    # 1. Every skill in the two lists, exactly as before alternatives existed.
    #    "required" first: if the LLM lists a skill in both lists (or two names
    #    collapse into one canonical skill), the first link wins — required.
    #    The alias table decides the names ("Microsoft Azure" -> "Azure",
    #    "C/C++" -> "C" and "C++"), not the LLM.
    links = {}  # skill id -> its AdSkill row in this ad
    for level, key in [("required", "required_skills"), ("nice_to_have", "nice_to_have_skills")]:
        for name in data.get(key) or []:
            if not isinstance(name, str):
                continue
            for canonical in canonical_names(name):
                skill = skill_named(canonical)
                if skill.id not in links:
                    links[skill.id] = AdSkill.objects.create(ad=ad, skill=skill, level=level)

    # 2. The "alternatives" note: which of those skills the ad accepts any ONE
    #    of. It only ever ADDS information. A wrong or missing note leaves the
    #    skills above exactly as they were — the worst case is how things were
    #    before. (A first version had the model MOVE skills into groups; it
    #    sometimes kept only the first of "Go or Python" and lost the rest.)
    groups = 0
    for group in data.get("alternatives") or []:
        if not isinstance(group, list):
            continue
        members = []
        for name in group:
            if isinstance(name, str):
                members += [s for s in map(skill_named, canonical_names(name)) if s not in members]
        # A skill named only in the note is still a skill the ad asks for:
        # add it, at the level of the group's other members.
        level = next((links[s.id].level for s in members if s.id in links), "required")
        for skill in members:
            if skill.id not in links:
                links[skill.id] = AdSkill.objects.create(ad=ad, skill=skill, level=level)
        # The group: its members at that level not already in another group.
        free = [links[s.id] for s in members if links[s.id].level == level and links[s.id].alternative_group is None]
        if len(free) >= 2:  # "one of" a single skill is just that skill
            groups += 1
            for link in free:
                link.alternative_group = groups
                link.save(update_fields=["alternative_group"])


def skill_named(name):
    """The Skill row for this name, ignoring case ("cloud" reuses "Cloud").

    Without this, every new capitalisation the LLM came up with became a new
    skill and split the statistics.
    """
    if case_matters(name):  # LoRA is not LoRa — see skill_aliases.CASE_SENSITIVE_NAMES
        return Skill.objects.get_or_create(name=name)[0]
    skill = Skill.objects.filter(name__iexact=name).order_by("id").first()
    return skill or Skill.objects.create(name=name)
