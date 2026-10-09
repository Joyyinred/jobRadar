"""
Day 9: the adapters, ingest() for non-paste sources, and the import endpoints.

No test ever calls the real JobTech API (it can be down, and ads disappear):
`fake_jobtech` replaces only the lowest step, JobTechClient._get (the HTTP
call). Everything above it — status handling, JSON -> RawJobAd, duplicates —
is the real code.
"""
from datetime import datetime

import pytest
import requests
from django.utils.timezone import is_aware

from ads import services
from ads.adapters import (
    JobTechClient,
    JobTechSearchAdapter,
    PasteAdapter,
    PlatsbankenUrlAdapter,
    RawJobAd,
)
from ads.exceptions import NotFoundError, UpstreamError, ValidationError
from ads.models import JobAd
from ads.tests.test_api import assert_error
from ads.tests.test_services import LONG_AD

LINK = "https://arbetsformedlingen.se/platsbanken/annonser/31575359"


def jobtech_ad(ad_id=31575359, headline="Junior Java-utvecklare", text=LONG_AD):
    """A JobTech ad as the API returns it (only the fields we read)."""
    return {
        "id": ad_id,
        "headline": headline,
        "employer": {"name": "Testbolaget AB"},
        "workplace_address": {"municipality": "Uppsala"},
        "description": {"text": text},
        "webpage_url": f"https://arbetsformedlingen.se/platsbanken/annonser/{ad_id}",
        "last_publication_date": "2026-12-08T23:59:59",
    }


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


@pytest.fixture
def fake_jobtech(monkeypatch):
    """Replace JobTech's HTTP layer. Set .ads (by id) and .hits (search results),
    or .status to make every call answer with that HTTP status."""

    class Fake:
        ads = {}
        hits = []
        status = 200
        calls = []

    def fake_get(self, path, params=None):
        Fake.calls.append(path)
        if Fake.status != 200:
            return FakeResponse(Fake.status)
        if path == "/search":
            return FakeResponse(200, {"hits": Fake.hits})
        ad_id = path.rsplit("/", 1)[-1]
        return FakeResponse(200, Fake.ads[ad_id]) if ad_id in Fake.ads else FakeResponse(404)

    monkeypatch.setattr(JobTechClient, "_get", fake_get)
    Fake.ads, Fake.hits, Fake.calls = {}, [], []
    return Fake


# --- Adapters (no database) ------------------------------------------------------

def test_paste_adapter_passes_the_text_through():
    [raw] = PasteAdapter("An ad", "https://example.com/1").fetch()
    assert raw == RawJobAd(raw_text="An ad", source="manual_paste", source_url="https://example.com/1")


def test_jobtech_json_becomes_a_raw_job_ad():
    raw = JobTechClient().to_raw(jobtech_ad())
    assert raw.source == "jobtech_api"
    assert raw.source_external_id == "31575359"  # a string, whatever JobTech sends
    assert raw.source_url == LINK
    # Headline, employer and city on top: the description may not repeat them.
    assert raw.raw_text.startswith("Junior Java-utvecklare\nTestbolaget AB\nUppsala\n\n")
    assert raw.expires_at == datetime(2026, 12, 8, 23, 59, 59, tzinfo=raw.expires_at.tzinfo)
    assert is_aware(raw.expires_at)


def test_jobtech_ad_without_optional_fields_still_translates():
    raw = JobTechClient().to_raw({"id": 1, "description": {"text": "Only a description."}})
    assert "Only a description." in raw.raw_text
    assert raw.expires_at is None


def test_platsbanken_link_gives_the_id_before_any_fetch(fake_jobtech):
    adapter = PlatsbankenUrlAdapter(LINK + "?utm_source=mail")
    assert adapter.external_id == "31575359"
    assert fake_jobtech.calls == []


@pytest.mark.parametrize("url", ["https://www.linkedin.com/jobs/view/123", "", "platsbanken"])
def test_non_platsbanken_link_is_rejected(url):
    with pytest.raises(ValidationError) as info:
        PlatsbankenUrlAdapter(url)
    assert info.value.code == "NOT_A_PLATSBANKEN_LINK"


def test_removed_ad_is_not_found(fake_jobtech):
    with pytest.raises(NotFoundError) as info:
        PlatsbankenUrlAdapter(LINK).fetch()
    assert info.value.code == "AD_NOT_ON_PLATSBANKEN"


def test_jobtech_error_status_is_upstream_error(fake_jobtech):
    fake_jobtech.status = 503
    with pytest.raises(UpstreamError):
        JobTechSearchAdapter("java").fetch()


def test_jobtech_unreachable_is_upstream_error(monkeypatch):
    def down(*args, **kwargs):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr("ads.adapters.requests.get", down)
    with pytest.raises(UpstreamError) as info:
        JobTechClient().get_ad("1")
    assert info.value.code == "JOBTECH_UNAVAILABLE"


# --- ingest() with sources other than paste ---------------------------------------

pytestmark_db = pytest.mark.django_db


@pytestmark_db
def test_jobtech_ad_is_stored_with_its_source_fields(enqueued):
    ad, created = services.ingest(JobTechClient().to_raw(jobtech_ad()))
    assert created
    ad.refresh_from_db()
    assert (ad.source, ad.source_external_id, ad.source_url) == ("jobtech_api", "31575359", LINK)
    assert ad.expires_at is not None
    assert ad.removed_at is None
    assert enqueued == [ad.id]


@pytestmark_db
def test_same_external_id_is_a_duplicate_even_if_the_text_changed(enqueued):
    first, _ = services.ingest(JobTechClient().to_raw(jobtech_ad()))
    again, created = services.ingest(JobTechClient().to_raw(jobtech_ad(text=LONG_AD + " Edited.")))
    assert not created
    assert again.id == first.id
    assert JobAd.objects.count() == 1


@pytestmark_db
def test_too_long_ad_is_rejected_even_without_a_serializer(enqueued):
    """The Day 9 hole: imports never pass through CreateAdSerializer, so the
    length rule must live in ingest() too."""
    raw = JobTechClient().to_raw(jobtech_ad(text="a" * services.MAX_AD_CHARS))
    with pytest.raises(ValidationError) as info:
        services.ingest(raw)
    assert info.value.code == "AD_TOO_LONG"
    assert JobAd.objects.count() == 0


@pytestmark_db
def test_known_platsbanken_ad_is_returned_without_fetching(enqueued, fake_jobtech):
    """Already imported -> no network call; works even after the ad was taken
    down at Platsbanken (where a fetch would now be a 404)."""
    first, _ = services.ingest(JobTechClient().to_raw(jobtech_ad()))
    ad, created = services.ingest_one(PlatsbankenUrlAdapter(LINK))
    assert not created
    assert ad.id == first.id
    assert fake_jobtech.calls == []


@pytestmark_db
def test_batch_counts_new_duplicate_and_skipped(enqueued, fake_jobtech):
    services.ingest(JobTechClient().to_raw(jobtech_ad(ad_id=1)))  # already have #1
    enqueued.clear()
    fake_jobtech.hits = [
        jobtech_ad(ad_id=1),                                   # duplicate
        jobtech_ad(ad_id=2, text=LONG_AD + " Two."),           # new
        jobtech_ad(ad_id=3, text=LONG_AD + " Three."),         # new
        jobtech_ad(ad_id=4, text="a" * services.MAX_AD_CHARS),  # too long -> skipped
        jobtech_ad(ad_id=5, headline="", text="Tiny."),        # too short, no one to confirm -> skipped
    ]
    counts = services.ingest_batch(JobTechSearchAdapter("java", 5))
    assert counts == {"created": 2, "duplicates": 1, "skipped": 2}
    assert len(enqueued) == 2


@pytestmark_db
def test_running_the_same_batch_twice_creates_nothing_the_second_time(enqueued, fake_jobtech):
    fake_jobtech.hits = [jobtech_ad(ad_id=i, text=f"{LONG_AD} {i}") for i in range(1, 4)]
    assert services.ingest_batch(JobTechSearchAdapter("java", 3))["created"] == 3
    assert services.ingest_batch(JobTechSearchAdapter("java", 3)) == {"created": 0, "duplicates": 3, "skipped": 0}


# --- The import endpoints --------------------------------------------------------

PLATSBANKEN = "/api/imports/platsbanken/"
SEARCH = "/api/imports/jobtech-search/"


@pytestmark_db
def test_import_platsbanken_link_is_202_then_200_duplicate(api, enqueued, fake_jobtech):
    fake_jobtech.ads = {"31575359": jobtech_ad()}
    first = api.post(PLATSBANKEN, {"url": LINK}, format="json")
    assert first.status_code == 202
    again = api.post(PLATSBANKEN, {"url": LINK}, format="json")
    assert again.status_code == 200
    assert again.json()["duplicate"] is True
    assert again.json()["id"] == first.json()["id"]


@pytestmark_db
def test_import_linkedin_link_is_400(api):
    assert_error(api.post(PLATSBANKEN, {"url": "https://www.linkedin.com/jobs/view/1"}, format="json"),
                 400, "validation", "NOT_A_PLATSBANKEN_LINK")


@pytestmark_db
def test_import_removed_ad_is_404(api, fake_jobtech):
    assert_error(api.post(PLATSBANKEN, {"url": LINK}, format="json"), 404, "not_found", "AD_NOT_ON_PLATSBANKEN")


@pytestmark_db
def test_import_when_jobtech_is_down_is_502(api, fake_jobtech):
    fake_jobtech.status = 500
    assert_error(api.post(PLATSBANKEN, {"url": LINK}, format="json"), 502, "upstream", "JOBTECH_UNAVAILABLE")


@pytestmark_db
def test_import_short_ad_warns_then_confirm_imports(api, enqueued, fake_jobtech):
    fake_jobtech.ads = {"31575359": jobtech_ad(headline="", text="Tiny ad.")}
    assert_error(api.post(PLATSBANKEN, {"url": LINK}, format="json"), 409, "warning", "AD_TOO_SHORT")
    assert api.post(PLATSBANKEN, {"url": LINK, "confirm": True}, format="json").status_code == 202


@pytestmark_db
def test_search_import_returns_counts(api, enqueued, fake_jobtech):
    fake_jobtech.hits = [jobtech_ad(ad_id=i, text=f"{LONG_AD} {i}") for i in range(1, 3)]
    response = api.post(SEARCH, {"query": "java", "limit": 2}, format="json")
    assert response.status_code == 200
    assert response.json() == {"created": 2, "duplicates": 0, "skipped": 0}


@pytestmark_db
@pytest.mark.parametrize("limit", [0, 101])
def test_search_import_limit_must_be_1_to_100(api, limit):
    assert_error(api.post(SEARCH, {"query": "java", "limit": limit}, format="json"), 400, "validation", "INVALID_INPUT")
