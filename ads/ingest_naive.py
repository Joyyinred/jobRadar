"""
Day 9, exercise 1: three sources, written the naive way — one function per
source, each doing every step itself. Nothing shared, on purpose.

This file is the "before" picture for the Adapter refactor. Read it and count
how much repeats. It is not wired to any URL; try it from the shell:

    python manage.py shell -c "from ads.ingest_naive import *; print(ingest_from_platsbanken_url('https://arbetsformedlingen.se/platsbanken/annonser/31575359'))"
"""
import hashlib
import logging
import re

import requests
from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime
from django.utils.timezone import make_aware

from .exceptions import NotFoundError, UpstreamError, ValidationError, WarningException
from .models import JobAd
from .services import enqueue_parse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source 1: pasted text (LinkedIn, company sites, ...)
# ---------------------------------------------------------------------------

def ingest_from_paste(raw_text, source_url=None, confirm=False):
    # 1. Validate
    if not raw_text or not raw_text.strip():
        raise ValidationError("INVALID_INPUT", "The ad is empty.")
    if len(raw_text) > 50_000:
        raise ValidationError("INVALID_INPUT", "The ad is over 50,000 characters.")

    # 2. Fingerprint
    normalised = re.sub(r"\s+", " ", raw_text.replace("\r\n", "\n")).strip()
    content_hash = hashlib.sha256(normalised.encode("utf-8")).hexdigest()

    # 3. Duplicate: same text
    existing = JobAd.objects.filter(content_hash=content_hash).first()
    if existing:
        return existing, False

    # 4. Warning: same URL, different text
    if source_url and not confirm:
        existing = JobAd.objects.filter(source_url=source_url).first()
        if existing:
            raise WarningException("URL_ALREADY_USED", f"Ad #{existing.id} came from this link. Save anyway?")

    # 5. Warning: too short
    if len(normalised) < 200 and not confirm:
        raise WarningException("AD_TOO_SHORT", "This may not be a whole ad. Parse it anyway?")

    # 6. Save
    try:
        with transaction.atomic():
            ad = JobAd.objects.create(
                raw_text=raw_text, content_hash=content_hash, source_url=source_url,
                source="manual_paste", status="pending",
            )
    except IntegrityError:
        return JobAd.objects.get(content_hash=content_hash), False

    # 7. Queue for parsing
    enqueue_parse(ad.id)
    logger.info("ad %s saved from paste", ad.id)
    return ad, True


# ---------------------------------------------------------------------------
# Source 2: one Platsbanken link -> the JobTech API
# ---------------------------------------------------------------------------

def ingest_from_platsbanken_url(url):
    # 1. Get the ad id out of the link
    match = re.search(r"platsbanken/annonser/(\d+)", url or "")
    if not match:
        raise ValidationError("INVALID_INPUT", "That is not a Platsbanken ad link.")
    external_id = match.group(1)

    # 2. Duplicate: already imported this JobTech ad?
    existing = JobAd.objects.filter(source="jobtech_api", source_external_id=external_id).first()
    if existing:
        return existing, False

    # 3. Fetch it
    try:
        response = requests.get(f"https://jobsearch.api.jobtechdev.se/ad/{external_id}", timeout=10)
    except requests.RequestException:
        raise UpstreamError("JOBTECH_UNAVAILABLE", "Could not reach Platsbanken. Try again later.")
    if response.status_code == 404:
        # JobTech answers 404 both for removed ads and ids that never existed.
        raise NotFoundError("AD_NOT_ON_PLATSBANKEN", "That ad is no longer on Platsbanken (or never was).")
    if response.status_code != 200:
        raise UpstreamError("JOBTECH_UNAVAILABLE", "Platsbanken returned an error. Try again later.")
    data = response.json()

    # 4. Turn JobTech's JSON into one text for the LLM (headline + employer +
    #    city are separate fields there; the description alone may not name them)
    raw_text = "\n".join([
        data.get("headline") or "",
        (data.get("employer") or {}).get("name") or "",
        (data.get("workplace_address") or {}).get("municipality") or "",
        "",
        (data.get("description") or {}).get("text") or "",
    ])

    # 5. Validate
    if not raw_text.strip():
        raise ValidationError("INVALID_INPUT", "The ad is empty.")
    if len(raw_text) > 50_000:
        raise ValidationError("INVALID_INPUT", "The ad is over 50,000 characters.")

    # 6. Fingerprint
    normalised = re.sub(r"\s+", " ", raw_text.replace("\r\n", "\n")).strip()
    content_hash = hashlib.sha256(normalised.encode("utf-8")).hexdigest()

    # 7. Duplicate: same text (e.g. it was pasted earlier)
    existing = JobAd.objects.filter(content_hash=content_hash).first()
    if existing:
        return existing, False

    # 8. Save
    expires = parse_datetime(data["last_publication_date"]) if data.get("last_publication_date") else None
    try:
        with transaction.atomic():
            ad = JobAd.objects.create(
                raw_text=raw_text, content_hash=content_hash,
                source_url=data.get("webpage_url") or url,
                source="jobtech_api", source_external_id=external_id,
                expires_at=make_aware(expires) if expires else None,
                status="pending",
            )
    except IntegrityError:
        return JobAd.objects.get(content_hash=content_hash), False

    # 9. Queue for parsing
    enqueue_parse(ad.id)
    logger.info("ad %s saved from Platsbanken %s", ad.id, external_id)
    return ad, True


# ---------------------------------------------------------------------------
# Source 3: a JobTech search -> many ads at once
# ---------------------------------------------------------------------------

def ingest_from_jobtech_search(query, limit=20):
    # 1. Validate the request
    if not 1 <= limit <= 100:
        raise ValidationError("INVALID_INPUT", "limit must be between 1 and 100.")

    # 2. Fetch one page of results
    try:
        response = requests.get(
            "https://jobsearch.api.jobtechdev.se/search",
            params={"q": query, "limit": limit},
            timeout=10,
        )
    except requests.RequestException:
        raise UpstreamError("JOBTECH_UNAVAILABLE", "Could not reach Platsbanken. Try again later.")
    if response.status_code != 200:
        raise UpstreamError("JOBTECH_UNAVAILABLE", "Platsbanken returned an error. Try again later.")
    hits = response.json().get("hits", [])

    created = duplicates = skipped = 0
    for data in hits:
        external_id = str(data["id"])

        # 3. Duplicate: already imported this JobTech ad?
        if JobAd.objects.filter(source="jobtech_api", source_external_id=external_id).exists():
            duplicates += 1
            continue

        # 4. Turn JobTech's JSON into one text for the LLM
        raw_text = "\n".join([
            data.get("headline") or "",
            (data.get("employer") or {}).get("name") or "",
            (data.get("workplace_address") or {}).get("municipality") or "",
            "",
            (data.get("description") or {}).get("text") or "",
        ])

        # 5. Validate (skip bad ones instead of failing the whole batch)
        if not raw_text.strip() or len(raw_text) > 50_000:
            skipped += 1
            continue

        # 6. Fingerprint
        normalised = re.sub(r"\s+", " ", raw_text.replace("\r\n", "\n")).strip()
        content_hash = hashlib.sha256(normalised.encode("utf-8")).hexdigest()

        # 7. Duplicate: same text
        if JobAd.objects.filter(content_hash=content_hash).exists():
            duplicates += 1
            continue

        # 8. Save
        expires = parse_datetime(data["last_publication_date"]) if data.get("last_publication_date") else None
        try:
            with transaction.atomic():
                ad = JobAd.objects.create(
                    raw_text=raw_text, content_hash=content_hash,
                    source_url=data.get("webpage_url"),
                    source="jobtech_api", source_external_id=external_id,
                    expires_at=make_aware(expires) if expires else None,
                    status="pending",
                )
        except IntegrityError:
            duplicates += 1
            continue

        # 9. Queue for parsing
        enqueue_parse(ad.id)
        created += 1

    logger.info("JobTech search %r: %d new, %d duplicates, %d skipped", query, created, duplicates, skipped)
    return {"created": created, "duplicates": duplicates, "skipped": skipped}
