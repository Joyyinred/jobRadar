"""
The original ad next to the parse: full text, how to apply, deadline.
"""
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from ads import services
from ads.adapters import JobTechClient
from ads.models import ImportJob, JobAd
from ads.tests.test_adapters import fake_jobtech, jobtech_ad  # noqa: F401 (fixture)
from ads.tests.test_services import LONG_AD, make_ad

pytestmark = pytest.mark.django_db

ADS = "/api/ads/"


def with_apply(url=None, email=None, **kwargs):
    ad = jobtech_ad(**kwargs)
    ad["application_details"] = {"url": url, "email": email}
    return ad


# --- Reading application details from JobTech -------------------------------------

def test_apply_link_and_email_come_from_application_details():
    raw = JobTechClient().to_raw(with_apply("https://jobs.example.com/apply/1", "jobs@example.com"))
    assert (raw.apply_url, raw.apply_email) == ("https://jobs.example.com/apply/1", "jobs@example.com")


@pytest.mark.parametrize("url", [
    "javascript:alert(document.cookie)",  # would run in our page when clicked
    "data:text/html,<script>alert(1)</script>",
    "www.example.com/no-scheme",
    "",
    None,
])
def test_only_http_links_are_kept(url):
    assert JobTechClient().to_raw(with_apply(url)).apply_url == ""


def test_something_that_isnt_an_email_is_dropped():
    assert JobTechClient().to_raw(with_apply(email="send your CV to Anna")).apply_email == ""


def test_no_application_details_at_all():
    assert JobTechClient().to_raw(jobtech_ad()).apply_url == ""


def test_import_stores_the_apply_link(enqueued, fake_jobtech):  # noqa: F811
    fake_jobtech.hits = [with_apply("https://jobs.example.com/apply/1")]
    services.run_jobtech_import(ImportJob.objects.create(params={"query": "", "limit": 1}).id)
    assert JobAd.objects.get().apply_url == "https://jobs.example.com/apply/1"


# --- GET /api/ads/<id>/ ------------------------------------------------------------

def test_detail_has_the_original_text_and_links(api):
    ad = make_ad(
        status="completed", source="jobtech_api", source_url="https://arbetsformedlingen.se/platsbanken/annonser/1",
        apply_url="https://jobs.example.com/apply/1",
    )
    listing = api.get(f"{ADS}{ad.id}/").json()["listing"]
    assert listing["text"] == LONG_AD
    assert listing["ad_url"] == "https://arbetsformedlingen.se/platsbanken/annonser/1"
    assert listing["apply_url"] == "https://jobs.example.com/apply/1"
    assert (listing["apply_email"], listing["apply_by"], listing["removed_at"]) == (None, None, None)


def test_listing_is_there_before_the_parse_finishes(api):
    """You can read the ad while it's still waiting for the LLM."""
    ad = make_ad(status="pending")
    assert api.get(f"{ADS}{ad.id}/").json()["listing"]["text"] == LONG_AD


def test_post_reply_does_not_echo_the_whole_ad(api, enqueued):
    body = api.post(ADS, {"raw_text": LONG_AD}, format="json").json()
    assert "listing" not in body


# --- backfill_jobtech: fills the new fields for old ads ---------------------------

def test_backfill_fills_apply_link_and_keeps_the_text(fake_jobtech):  # noqa: F811
    ad = make_ad(source="jobtech_api", source_external_id="31575359")
    fake_jobtech.ads = {"31575359": with_apply("https://jobs.example.com/apply/1", text="different text now")}
    call_command("backfill_jobtech")
    ad.refresh_from_db()
    assert ad.apply_url == "https://jobs.example.com/apply/1"
    assert ad.occupation_group == ""  # jobtech_ad() has no group: copied as it is
    assert ad.raw_text == LONG_AD  # the parsed text is never rewritten


def test_backfill_marks_ads_gone_from_jobtech_as_removed(fake_jobtech):  # noqa: F811
    ad = make_ad(source="jobtech_api", source_external_id="999")  # fake_jobtech: unknown id -> 404
    call_command("backfill_jobtech")
    ad.refresh_from_db()
    assert ad.removed_at is not None


def test_backfill_leaves_pasted_ads_alone(fake_jobtech):  # noqa: F811
    make_ad()
    call_command("backfill_jobtech")
    assert fake_jobtech.calls == []


# --- "closed" in the list -----------------------------------------------------------

def test_list_marks_ads_you_can_no_longer_apply_to(api):
    now = timezone.now()
    open_ad = make_ad(raw_text=f"{LONG_AD} open", expires_at=now + timedelta(days=3))
    past_deadline = make_ad(raw_text=f"{LONG_AD} late", expires_at=now - timedelta(days=1))
    taken_down = make_ad(raw_text=f"{LONG_AD} gone", removed_at=now)
    pasted = make_ad(raw_text=f"{LONG_AD} pasted")  # no dates: unknown counts as open
    closed = {r["id"]: r["closed"] for r in api.get(ADS).json()["results"]}
    assert closed == {open_ad.id: False, past_deadline.id: True, taken_down.id: True, pasted.id: False}
