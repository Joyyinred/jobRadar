"""
degree_level: bachelor / master / either — for thesis ads, internships and jobs.
"""
import pytest
from django.core.management import call_command

from ads import services
from ads.models import JobAd
from ads.tests.test_services import LONG_AD, make_ad

pytestmark = pytest.mark.django_db

ADS = "/api/ads/"


def ad(n, degree_level, **fields):
    return make_ad(raw_text=f"{LONG_AD} {n}", status="completed", degree_level=degree_level, **fields)


def ids(response):
    return sorted(a["id"] for a in response.json()["results"])


def test_parse_stores_the_degree_level():
    a = make_ad()
    services.save_parsed(a, {"title": "Exjobb", "employment_type": "thesis", "degree_level": "master"})
    a.refresh_from_db()
    assert a.degree_level == "master"


def test_master_filter_includes_ads_open_to_either_level(api):
    """A master student looking for a thesis wants "bachelor's or master's" ads too."""
    master, either = ad(1, "master"), ad(2, "either")
    ad(3, "bachelor"), ad(4, "unspecified")
    assert ids(api.get(f"{ADS}?degree_level=master")) == sorted([master.id, either.id])


def test_bachelor_filter_includes_ads_open_to_either_level(api):
    bachelor, either = ad(1, "bachelor"), ad(2, "either")
    ad(3, "master")
    assert ids(api.get(f"{ADS}?degree_level=bachelor")) == sorted([bachelor.id, either.id])


def test_degree_filter_combines_with_employment_type(api):
    """The question it's for: "master's thesis ads"."""
    thesis = ad(1, "master", employment_type="thesis")
    ad(2, "master", employment_type="permanent")
    ad(3, "bachelor", employment_type="thesis")
    assert ids(api.get(f"{ADS}?degree_level=master&employment_type=thesis")) == [thesis.id]


def test_either_is_not_a_filter_value(api):
    """"either" is what an AD can say; a person is filtering for their own level."""
    assert api.get(f"{ADS}?degree_level=either").status_code == 400


def test_list_and_detail_show_the_degree_level(api):
    a = ad(1, "master", employment_type="thesis")
    assert api.get(ADS).json()["results"][0]["degree_level"] == "master"
    assert api.get(f"{ADS}{a.id}/").json()["result"]["degree_level"] == "master"


def test_reparse_students_queues_only_thesis_internship_and_intern_ads(enqueued):
    thesis = make_ad(raw_text=f"{LONG_AD} t", status="completed", employment_type="thesis")
    internship = make_ad(raw_text=f"{LONG_AD} i", status="completed", employment_type="internship")
    intern = make_ad(raw_text=f"{LONG_AD} n", status="completed", employment_type="fixed_term", seniority="intern")
    make_ad(raw_text=f"{LONG_AD} j", status="completed", employment_type="permanent", seniority="junior")
    call_command("reparse", students=True)
    assert sorted(enqueued) == sorted([thesis.id, internship.id, intern.id])
    assert JobAd.objects.filter(status="pending").count() == 3


def test_reparse_missing_degree_level_skips_ads_already_asked(enqueued):
    never_asked = make_ad(raw_text=f"{LONG_AD} a", status="completed", degree_level="")
    make_ad(raw_text=f"{LONG_AD} b", status="completed", degree_level="unspecified")  # asked: ad doesn't say
    make_ad(raw_text=f"{LONG_AD} c", status="completed", degree_level="master")
    make_ad(raw_text=f"{LONG_AD} d", status="failed", degree_level="")  # not parsed: retry is for that
    call_command("reparse", missing_degree_level=True)
    assert enqueued == [never_asked.id]
