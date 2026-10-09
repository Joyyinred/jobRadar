"""
Day 9: the Adapter pattern for job-ad sources.

Every source speaks its own language — pasted text, a Platsbanken link, a
JobTech search result page. An adapter's ONLY job is to translate it into one
shape, RawJobAd. Everything after that (validation, hashing, duplicates,
saving, queueing) is written once, in services.ingest(), which never knows
where an ad came from.

    PasteAdapter           ─┐
    PlatsbankenUrlAdapter  ─┼─▶ RawJobAd ─▶ services.ingest()
    JobTechSearchAdapter   ─┘

Adding a source = adding one adapter class. Nothing downstream changes.
"""
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

import requests
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive, make_aware

from .exceptions import NotFoundError, UpstreamError, ValidationError


# --- The one internal format ---------------------------------------------------

@dataclass(frozen=True)
class RawJobAd:
    """One job ad as the rest of the app sees it, whatever the source.

    frozen=True: once an adapter has built it, nothing can change it on the
    way to the database (the raw text is the source of truth).
    """

    raw_text: str
    source: str                             # "manual_paste" | "jobtech_api" (JobAd.SOURCE_CHOICES)
    source_url: str | None = None
    source_external_id: str | None = None   # the ad's id at the source, if it has one
    expires_at: datetime | None = None      # when the source plans to take it down


# --- The interface every adapter implements -------------------------------------

class JobAdAdapter(ABC):
    """Base class: "something that produces RawJobAds"."""

    # Which JobAd.source this adapter's ads get ("manual_paste", "jobtech_api").
    source: str

    # Set when the source's id is known BEFORE fetching (e.g. read from a link),
    # so services can skip the network call for an ad it already has — even one
    # that has since been taken down at the source.
    external_id: str | None = None

    @abstractmethod
    def fetch(self) -> list[RawJobAd]:
        """Go to the source and return its ads in the internal format."""


# --- Source 1: pasted text ------------------------------------------------------

class PasteAdapter(JobAdAdapter):
    """Text pasted into the form — LinkedIn, company sites, anything."""

    source = "manual_paste"

    def __init__(self, raw_text, source_url=None):
        self.raw_text = raw_text
        self.source_url = source_url

    def fetch(self):
        # Nothing to fetch or translate: the user handed us the text itself.
        return [RawJobAd(raw_text=self.raw_text, source=self.source, source_url=self.source_url)]


# --- JobTech: the API both JobTech adapters share --------------------------------

class JobTechClient:
    """Talks to the JobTech JobSearch API (Platsbanken's open data).

    Shared by the two JobTech adapters, so the HTTP calls, the error handling
    and the JSON -> RawJobAd translation each exist exactly once.
    No API key needed (checked 2026-10-05).
    """

    BASE_URL = "https://jobsearch.api.jobtechdev.se"
    TIMEOUT_SECONDS = 10

    def get_ad(self, external_id):
        """One ad by id. NotFoundError if Platsbanken doesn't have it (any more)."""
        response = self._get(f"/ad/{external_id}")
        if response.status_code == 404:
            # JobTech answers 404 both for removed ads and for ids that never existed.
            raise NotFoundError(
                "AD_NOT_ON_PLATSBANKEN",
                "That ad is no longer on Platsbanken (or never was).",
                {"external_id": external_id},
            )
        return self._json(response)

    def search(self, query, limit):
        """One page of search results (JobTech allows up to 100 per page)."""
        response = self._get("/search", params={"q": query, "limit": limit})
        return self._json(response).get("hits", [])

    def to_raw(self, ad):
        """JobTech JSON -> RawJobAd.

        Headline, employer and city are separate fields at JobTech and the
        description doesn't always repeat them, so they go on top of the text
        the LLM reads.
        """
        raw_text = "\n".join([
            ad.get("headline") or "",
            (ad.get("employer") or {}).get("name") or "",
            (ad.get("workplace_address") or {}).get("municipality") or "",
            "",
            (ad.get("description") or {}).get("text") or "",
        ])
        return RawJobAd(
            raw_text=raw_text,
            source="jobtech_api",
            source_url=ad.get("webpage_url"),
            source_external_id=str(ad["id"]),
            expires_at=_aware(ad.get("last_publication_date")),
        )

    def _get(self, path, params=None):
        try:
            return requests.get(self.BASE_URL + path, params=params, timeout=self.TIMEOUT_SECONDS)
        except requests.RequestException:
            # Down, slow, DNS... not the user's fault and not ours: 502.
            raise UpstreamError("JOBTECH_UNAVAILABLE", "Could not reach Platsbanken. Try again later.")

    def _json(self, response):
        if response.status_code != 200:
            raise UpstreamError(
                "JOBTECH_UNAVAILABLE", "Platsbanken returned an error. Try again later.",
                {"status": response.status_code},
            )
        return response.json()


def _aware(value):
    """JobTech dates come without a timezone ("2026-12-08T23:59:59"); they are
    Swedish time, which is settings.TIME_ZONE."""
    parsed = parse_datetime(value) if value else None
    if parsed is not None and is_naive(parsed):
        parsed = make_aware(parsed)
    return parsed


# --- Source 2: one Platsbanken link ---------------------------------------------

PLATSBANKEN_AD_URL = re.compile(r"platsbanken/annonser/(\d+)")


class PlatsbankenUrlAdapter(JobAdAdapter):
    """A Platsbanken ad link: https://arbetsformedlingen.se/platsbanken/annonser/31575359"""

    source = "jobtech_api"

    def __init__(self, url, client=None):
        match = PLATSBANKEN_AD_URL.search(url or "")
        if not match:
            raise ValidationError(
                "NOT_A_PLATSBANKEN_LINK", "That is not a Platsbanken ad link.", {"url": url},
            )
        self.external_id = match.group(1)  # known before fetching
        self.client = client or JobTechClient()

    def fetch(self):
        return [self.client.to_raw(self.client.get_ad(self.external_id))]


# --- Source 3: a JobTech search -------------------------------------------------

class JobTechSearchAdapter(JobAdAdapter):
    """Many ads at once: one page of a JobTech free-text search."""

    source = "jobtech_api"

    def __init__(self, query, limit=20, client=None):
        self.query = query
        self.limit = limit
        self.client = client or JobTechClient()

    def fetch(self):
        return [self.client.to_raw(ad) for ad in self.client.search(self.query, self.limit)]
