"""
The ad list in pages of 20, and telling apart ads you added from bulk imports.
"""
import pytest

from ads import services
from ads.models import ImportJob, JobAd
from ads.tests.test_adapters import LINK, fake_jobtech, jobtech_ad  # noqa: F401 (fixture)
from ads.tests.test_api import assert_error
from ads.tests.test_services import LONG_AD, make_ad

pytestmark = pytest.mark.django_db

ADS = "/api/ads/"


def make_ads(n, tag="", **fields):
    return [make_ad(raw_text=f"{LONG_AD} {tag}{i}", **fields) for i in range(n)]


# --- Paging ------------------------------------------------------------------------

def test_list_comes_in_pages_of_20(api):
    make_ads(45)
    body = api.get(ADS).json()
    assert (body["count"], body["page"], body["pages"], body["page_size"]) == (45, 1, 3, 20)
    assert len(body["results"]) == 20
    assert body["previous"] is None and body["next"].endswith("?page=2")


def test_last_page_has_the_rest_and_no_next(api):
    make_ads(45)
    body = api.get(f"{ADS}?page=3").json()
    assert len(body["results"]) == 5
    assert body["next"] is None


def test_pages_together_hold_every_ad_exactly_once(api):
    """Ads created in the same instant (a bulk import) still have one fixed
    order (created_at, then id) — none skipped, none on two pages."""
    ads = make_ads(45)
    JobAd.objects.update(created_at=ads[0].created_at)  # all the same timestamp
    seen = [a["id"] for page in (1, 2, 3) for a in api.get(f"{ADS}?page={page}").json()["results"]]
    assert sorted(seen) == sorted(a.id for a in ads)


def test_count_is_of_the_filtered_ads(api):
    make_ads(25, tag="junior", seniority="junior")
    make_ads(5, tag="senior", seniority="senior")
    body = api.get(f"{ADS}?seniority=senior").json()
    assert (body["count"], body["pages"]) == (5, 1)


def test_page_past_the_end_is_404(api):
    make_ads(3)
    assert_error(api.get(f"{ADS}?page=99"), 404, "not_found", "NOT_FOUND")


def test_no_ads_is_one_empty_page_not_an_error(api):
    body = api.get(ADS).json()
    assert (body["count"], body["results"]) == (0, [])


# --- Origin: added by you, or a bulk import ---------------------------------------

def test_pasted_ad_is_yours(enqueued):
    ad, _ = services.submit_ad(LONG_AD)
    assert ad.origin == "user"


def test_one_platsbanken_link_is_yours_too(api, enqueued, fake_jobtech):  # noqa: F811
    """Same source as a bulk import (jobtech_api) — but you chose it."""
    fake_jobtech.ads = {"31575359": jobtech_ad()}
    ad_id = api.post("/api/imports/platsbanken/", {"url": LINK}, format="json").json()["id"]
    assert JobAd.objects.get(id=ad_id).origin == "user"


def test_bulk_import_ads_are_marked_import(enqueued, fake_jobtech):  # noqa: F811
    fake_jobtech.hits = [jobtech_ad(ad_id=i, text=f"{LONG_AD} {i}") for i in (1, 2)]
    job = ImportJob.objects.create(params={"query": "java", "limit": 2, "data_it_only": False})
    services.run_jobtech_import(job.id)
    assert list(JobAd.objects.values_list("origin", flat=True)) == ["import", "import"]


def test_adding_an_already_imported_ad_makes_it_yours(api, enqueued, fake_jobtech):  # noqa: F811
    fake_jobtech.hits = [jobtech_ad()]
    services.run_jobtech_import(ImportJob.objects.create(params={"query": "", "limit": 1}).id)
    fake_jobtech.ads = {"31575359": jobtech_ad()}
    again = api.post("/api/imports/platsbanken/", {"url": LINK}, format="json")
    assert again.json()["duplicate"] is True  # still one ad, not two
    assert JobAd.objects.get().origin == "user"


def test_filter_by_origin(api):
    mine = make_ad(raw_text=f"{LONG_AD} mine", origin="user")
    make_ad(raw_text=f"{LONG_AD} imported", origin="import")
    rows = api.get(f"{ADS}?origin=user").json()["results"]
    assert [(r["id"], r["origin"]) for r in rows] == [(mine.id, "user")]


def test_unknown_origin_is_400(api):
    assert api.get(f"{ADS}?origin=robot").status_code == 400
