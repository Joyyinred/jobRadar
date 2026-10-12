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

from . import metrics
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

    def pages(self):
        """The same ads, one page at a time (a generator of lists).

        Lets a long import report progress page by page. By default the whole
        result is one page; adapters that fetch in pages override it.
        """
        yield self.fetch()


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
    TIMEOUT_SECONDS = 10         # one ad
    SEARCH_TIMEOUT_SECONDS = 30  # a page of search results is much bigger and slower

    def get_ad(self, external_id):
        """One ad by id. NotFoundError if Platsbanken doesn't have it (any more)."""
        response = self._get(f"/ad/{external_id}")
        if response.status_code == 404:
            # JobTech answers 404 both for removed ads and for ids that never existed.
            metrics.JOBTECH_REQUESTS.labels("not_found").inc()
            raise NotFoundError(
                "AD_NOT_ON_PLATSBANKEN",
                "That ad is no longer on Platsbanken (or never was).",
                {"external_id": external_id},
            )
        return self._json(response)

    def search(self, query, limit, offset=0, occupation_field=None):
        """One page of search results, newest first, starting at `offset`.

        Keep `limit` small: on 2026-10-10 a page of 10 full ads took ~2s, a
        page of 100 took ~60s (1.2 MB). JobTechSearchAdapter pages for you.
        """
        params = {"limit": limit, "offset": offset, "sort": "pubdate-desc"}
        if query:
            params["q"] = query
        if occupation_field:
            params["occupation-field"] = occupation_field
        response = self._get("/search", params=params, timeout=self.SEARCH_TIMEOUT_SECONDS)
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

    def _get(self, path, params=None, timeout=None):
        try:
            return requests.get(self.BASE_URL + path, params=params, timeout=timeout or self.TIMEOUT_SECONDS)
        except requests.RequestException:
            # Down, slow, DNS... not the user's fault and not ours: 502.
            metrics.JOBTECH_REQUESTS.labels("error").inc()
            raise UpstreamError("JOBTECH_UNAVAILABLE", "Could not reach Platsbanken. Try again later.")

    def _json(self, response):
        if response.status_code != 200:
            metrics.JOBTECH_REQUESTS.labels("error").inc()
            raise UpstreamError(
                "JOBTECH_UNAVAILABLE", "Platsbanken returned an error. Try again later.",
                {"status": response.status_code},
            )
        metrics.JOBTECH_REQUESTS.labels("ok").inc()
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

# JobTech's occupation-field code for "Data/IT" (from its taxonomy). Searching
# by field catches ads a keyword misses: on 2026-10-05, q="junior" in Data/IT
# found 60 ads, while Data/IT had 2,771.
DATA_IT_FIELD = "apaJ_2ja_LuF"


class JobTechSearchAdapter(JobAdAdapter):
    """Many ads at once: one page (newest first) of a JobTech search, by
    free text and/or occupation field."""

    source = "jobtech_api"

    def __init__(self, query="", limit=20, occupation_field=None, client=None):
        self.query = query
        self.limit = limit
        self.occupation_field = occupation_field
        self.client = client or JobTechClient()

    PAGE_SIZE = 20  # small pages: big ones are slow enough to time out (see JobTechClient.search)

    def pages(self):
        fetched = 0
        while fetched < self.limit:
            want = min(self.PAGE_SIZE, self.limit - fetched)
            page = self.client.search(
                self.query, want, offset=fetched, occupation_field=self.occupation_field,
            )
            fetched += len(page)
            yield [self.client.to_raw(ad) for ad in page]
            if len(page) < want:
                return  # JobTech has no more results

    def fetch(self):
        return [raw for page in self.pages() for raw in page]
