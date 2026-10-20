"""
Market statistics: the numbers must be exact, and the refusals honest.
"""
import pytest

from ads import market
from ads.exceptions import InsufficientDataError
from ads.models import AdSkill, JobAd, Skill
from ads.services import content_hash_of
from ads.tests.test_api import assert_error

pytestmark = pytest.mark.django_db

MARKET = "/api/market/skills/"


def make_ads(n, seniority="junior", swedish="required", skills=(), start=0):
    """n completed ads, each linking the given (name, level) skills."""
    for i in range(start, start + n):
        text = f"ad {seniority} {i}"
        ad = JobAd.objects.create(
            raw_text=text, content_hash=content_hash_of(text), status="completed",
            seniority=seniority, swedish_requirement=swedish,
        )
        for name, level in skills:
            skill, _ = Skill.objects.get_or_create(name=name)
            AdSkill.objects.create(ad=ad, skill=skill, level=level)


def by_name(result):
    return {s["name"]: s for s in result["skills"]}


def test_fewer_than_min_sample_is_refused():
    make_ads(market.MIN_SAMPLE - 1)
    with pytest.raises(InsufficientDataError) as info:
        market.skill_frequency(seniority=["junior"])
    assert info.value.detail == {"sample_size": market.MIN_SAMPLE - 1, "min_sample": market.MIN_SAMPLE}


def test_share_is_ads_with_the_skill_over_all_matching_ads():
    make_ads(10, skills=[("Python", "required")])
    make_ads(30, start=10)  # no skills
    result = market.skill_frequency(seniority=["junior"])
    assert result["sample_size"] == 40
    assert by_name(result)["Python"] == {
        "name": "Python", "ads": 10, "share": 0.25, "required": 10, "one_of": 0, "nice_to_have": 0,
    }


def test_only_the_asked_seniority_is_counted():
    make_ads(30, seniority="junior", skills=[("Python", "required")])
    make_ads(30, seniority="senior", skills=[("Java", "required")])
    names = by_name(market.skill_frequency(seniority=["junior"]))
    assert "Python" in names and "Java" not in names


def test_spellings_differing_only_in_case_are_one_skill():
    make_ads(15, skills=[("Cloud", "required")])
    make_ads(15, start=15, skills=[("cloud", "nice_to_have")])
    cloud = by_name(market.skill_frequency())["Cloud"]
    assert (cloud["ads"], cloud["share"], cloud["required"], cloud["nice_to_have"]) == (30, 1.0, 15, 15)


def test_the_most_used_spelling_is_shown():
    make_ads(5, skills=[("Python", "required")])
    make_ads(25, start=5, skills=[("python", "required")])
    assert [s["name"] for s in market.skill_frequency()["skills"]] == ["python"]  # 25 > 5


def test_languages_are_not_ranked_as_skills_but_reported_separately():
    make_ads(20, swedish="required", skills=[("Swedish", "required"), ("svenska", "required"), ("Python", "required")])
    make_ads(10, start=20, swedish="not_mentioned")
    result = market.skill_frequency()
    assert set(by_name(result)) == {"Python"}
    assert result["swedish_requirement"] == {"required": 0.667, "preferred": 0.0, "not_mentioned": 0.333}


def test_level_filter_counts_only_that_level():
    make_ads(15, skills=[("AWS", "nice_to_have")])
    make_ads(15, start=15, skills=[("AWS", "required")])
    aws = by_name(market.skill_frequency(level="required"))["AWS"]
    assert (aws["ads"], aws["required"], aws["nice_to_have"]) == (15, 15, 0)


def test_top_limits_and_orders_by_ad_count():
    make_ads(30, skills=[("Python", "required")])
    make_ads(10, start=30, skills=[("Go", "required")])
    result = market.skill_frequency(top=1)
    assert [s["name"] for s in result["skills"]] == ["Python"]


# --- the endpoint ----------------------------------------------------------------

def test_endpoint_accepts_repeated_seniority(api):
    make_ads(20, seniority="junior", skills=[("Python", "required")])
    make_ads(15, seniority="intern", skills=[("Python", "required")])
    body = api.get(MARKET + "?seniority=junior&seniority=intern").json()
    assert body["sample_size"] == 35
    assert body["filters"]["seniority"] == ["junior", "intern"]
    assert "not the whole market" in body["note"]


def test_endpoint_small_sample_is_422_in_the_error_format(api):
    make_ads(5)
    body = assert_error(api.get(MARKET + "?seniority=junior"), 422, "insufficient_data", "SAMPLE_TOO_SMALL")
    assert body["detail"]["sample_size"] == 5


def test_endpoint_rejects_unknown_seniority(api):
    assert_error(api.get(MARKET + "?seniority=wizard"), 400, "validation", "INVALID_INPUT")
