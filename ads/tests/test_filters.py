"""
Filtering the ad list (seniority, employment type, JobTech occupation group)
and the occupation groups offered in the drop-downs.
"""
import pytest

from ads import market
from ads.models import JobAd
from ads.services import content_hash_of

pytestmark = pytest.mark.django_db

DEV = ("DJh5_yyF_hEM", "Mjukvaru- och systemutvecklare m.fl.")
SUPPORT = ("hmaC_cfi_UKg", "Supporttekniker, IT")
# What the API shows instead of JobTech's Swedish labels (ads/occupation_labels.py)
DEV_EN = "Software and systems developers"
SUPPORT_EN = "IT support technicians"


def ad(n, seniority="junior", employment_type="permanent", group=None):
    text = f"filter test ad {n}"
    code, label = group or ("", "")
    return JobAd.objects.create(
        raw_text=text, content_hash=content_hash_of(text), status="completed",
        seniority=seniority, employment_type=employment_type,
        occupation_group_code=code, occupation_group=label,
    )


def ids(response):
    return sorted(a["id"] for a in response.json())


def test_no_filter_lists_everything(api):
    a, b = ad(1), ad(2, seniority="senior")
    assert ids(api.get("/api/ads/")) == sorted([a.id, b.id])


def test_filter_by_several_seniorities(api):
    junior, intern, senior = ad(1), ad(2, seniority="intern"), ad(3, seniority="senior")
    assert ids(api.get("/api/ads/?seniority=junior&seniority=intern")) == sorted([junior.id, intern.id])


def test_filter_by_employment_type(api):
    thesis = ad(1, employment_type="thesis")
    ad(2, employment_type="permanent")
    assert ids(api.get("/api/ads/?employment_type=thesis")) == [thesis.id]


def test_filter_by_occupation_group(api):
    dev = ad(1, group=DEV)
    ad(2, group=SUPPORT)
    ad(3)  # pasted, no group
    assert ids(api.get(f"/api/ads/?occupation_group={DEV[0]}")) == [dev.id]


def test_filters_combine(api):
    match = ad(1, seniority="junior", employment_type="thesis", group=DEV)
    ad(2, seniority="junior", employment_type="permanent", group=DEV)
    ad(3, seniority="senior", employment_type="thesis", group=DEV)
    ad(4, seniority="junior", employment_type="thesis", group=SUPPORT)
    url = f"/api/ads/?seniority=junior&employment_type=thesis&occupation_group={DEV[0]}"
    assert ids(api.get(url)) == [match.id]


def test_unknown_employment_type_is_400(api):
    assert api.get("/api/ads/?employment_type=wizard").status_code == 400


def test_list_shows_the_filter_fields(api):
    ad(1, employment_type="thesis", group=DEV)
    row = api.get("/api/ads/").json()[0]
    assert (row["seniority"], row["employment_type"], row["occupation_group"]) == ("junior", "thesis", DEV_EN)


def test_occupation_groups_lists_groups_with_counts_most_first(api):
    ad(1, group=DEV), ad(2, group=DEV), ad(3, group=SUPPORT), ad(4)
    assert api.get("/api/occupation-groups/").json() == [
        {"code": DEV[0], "label": DEV_EN, "ads": 2},
        {"code": SUPPORT[0], "label": SUPPORT_EN, "ads": 1},
    ]  # the pasted ad (no group) isn't a group


def test_market_can_be_limited_to_one_occupation_group(api):
    for n in range(market.MIN_SAMPLE):
        ad(n, group=DEV)
    for n in range(100, 105):
        ad(n, group=SUPPORT)
    body = api.get(f"/api/market/skills/?occupation_group={DEV[0]}").json()
    assert body["sample_size"] == market.MIN_SAMPLE
    assert body["filters"]["occupation_group"] == DEV[0]
    # Support alone has 5 ads: refused, not a percentage of 5.
    assert api.get(f"/api/market/skills/?occupation_group={SUPPORT[0]}").status_code == 422


def test_a_group_whose_label_is_sometimes_missing_is_still_one_group(api):
    """JobTech occasionally sends a code without its label; that must not
    split the group (it did: 82 + 2, and the drop-down disagreed with the stats)."""
    ad(1, group=DEV), ad(2, group=DEV), ad(3, group=(DEV[0], ""))
    assert api.get("/api/occupation-groups/").json() == [{"code": DEV[0], "label": DEV_EN, "ads": 3}]


def test_ad_detail_shows_the_english_group_name(api):
    a = ad(1, group=DEV)
    assert api.get(f"/api/ads/{a.id}/").json()["result"]["occupation_group"] == DEV_EN
    # the database still holds what JobTech sent
    a.refresh_from_db()
    assert a.occupation_group == DEV[1]


def test_a_group_without_a_translation_keeps_its_swedish_label(api):
    """A new import can bring a group the table doesn't know yet: show it, in Swedish."""
    ad(1, group=("new_code_123", "Nya yrkesgruppen"))
    assert api.get("/api/occupation-groups/").json() == [
        {"code": "new_code_123", "label": "Nya yrkesgruppen", "ads": 1},
    ]
