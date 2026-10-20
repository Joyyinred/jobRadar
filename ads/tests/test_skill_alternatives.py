"""
"One of Python, Java or Go": alternatives are stored as a group, shown as a
group, and counted apart from firm requirements in the market statistics.
"""
import pytest
from django.core.management import call_command

from ads import market, services
from ads.models import AdSkill, Skill
from ads.tests.test_services import LONG_AD, make_ad

pytestmark = pytest.mark.django_db


def parse(data, n=0):
    ad = make_ad(raw_text=f"{LONG_AD} {n}", status="completed")
    services.save_parsed(ad, {"title": "Dev", **data})
    return ad


def links(ad):
    return {(s.skill.name, s.level, s.alternative_group) for s in ad.ad_skills.select_related("skill")}


# --- Storing -------------------------------------------------------------------------

def test_alternatives_mark_a_group_on_the_listed_skills():
    ad = parse({
        "required_skills": ["Docker", "Python", "Java", "Go"],
        "alternatives": [["Python", "Java", "Go"]],
    })
    assert links(ad) == {
        ("Docker", "required", None),
        ("Python", "required", 1), ("Java", "required", 1), ("Go", "required", 1),
    }


def test_a_skill_only_in_the_note_is_still_kept():
    """The model forgot Python in the list but named it in the note: kept."""
    ad = parse({"required_skills": ["Go"], "alternatives": [["Go", "Python"]]})
    assert links(ad) == {("Go", "required", 1), ("Python", "required", 1)}


def test_a_note_missing_a_skill_loses_nothing():
    """The failure that made us switch: the note names only some of the
    alternatives. The others are still there, as plain requirements."""
    ad = parse({"required_skills": ["AWS", "Azure", "GCP"], "alternatives": [["AWS"]]})
    assert links(ad) == {("AWS", "required", None), ("Azure", "required", None), ("GCP", "required", None)}


def test_no_note_is_exactly_the_old_behaviour():
    ad = parse({"required_skills": ["Python", "SQL"], "nice_to_have_skills": ["Docker"]})
    assert links(ad) == {("Python", "required", None), ("SQL", "required", None), ("Docker", "nice_to_have", None)}


def test_two_groups_get_two_numbers():
    ad = parse({
        "required_skills": ["Python", "Java", "AWS", "Azure"],
        "alternatives": [["Python", "Java"], ["AWS", "Azure"]],
    })
    assert {s.alternative_group for s in ad.ad_skills.all()} == {1, 2}


def test_a_group_stays_within_one_level():
    """Python required, Go nice-to-have, noted as alternatives: the levels win
    — they're from the lists, the part the model is reliable at."""
    ad = parse({"required_skills": ["Python"], "nice_to_have_skills": ["Go"], "alternatives": [["Python", "Go"]]})
    assert links(ad) == {("Python", "required", None), ("Go", "nice_to_have", None)}


def test_a_skill_is_in_one_group_at_most():
    ad = parse({
        "required_skills": ["Python", "Java", "Go"],
        "alternatives": [["Python", "Java"], ["Java", "Go"]],
    })
    assert links(ad) == {("Python", "required", 1), ("Java", "required", 1), ("Go", "required", None)}


def test_aliases_apply_inside_a_group():
    """"C/C++" splits into C and C++ — both stay alternatives."""
    ad = parse({"required_skills": ["Java", "C/C++", "Microsoft Azure"], "alternatives": [["Java", "C/C++", "Microsoft Azure"]]})
    assert {name for name, _, group in links(ad) if group == 1} == {"Java", "C", "C++", "Azure"}


def test_junk_in_the_note_is_skipped_not_fatal():
    ad = parse({"required_skills": ["Git", "Rust", "Go"], "alternatives": ["Rust", {"x": 1}, 7, [None, "Rust", "Go"]]})
    assert links(ad) == {("Git", "required", None), ("Rust", "required", 1), ("Go", "required", 1)}


# --- Showing -------------------------------------------------------------------------

def test_detail_shows_groups_apart_from_single_skills(api):
    ad = parse({
        "required_skills": ["Docker", "Python", "Java", "Go"],
        "nice_to_have_skills": ["Kubernetes"],
        "alternatives": [["Python", "Java", "Go"]],
    })
    result = api.get(f"/api/ads/{ad.id}/").json()["result"]
    assert result["required_skills"] == ["Docker"]
    assert result["required_one_of"] == [["Python", "Java", "Go"]]
    assert (result["nice_to_have_skills"], result["nice_to_have_one_of"]) == (["Kubernetes"], [])


# --- Counting ------------------------------------------------------------------------

def test_market_counts_alternatives_apart_from_requirements():
    """30 ads: 10 require Python on its own, 20 accept "Python or Java"."""
    for n in range(10):
        parse({"required_skills": ["Python"]}, n)
    for n in range(10, 30):
        parse({"required_skills": ["Python", "Java"], "alternatives": [["Python", "Java"]]}, n)
    skills = {s["name"]: s for s in market.skill_frequency()["skills"]}
    assert (skills["Python"]["ads"], skills["Python"]["required"], skills["Python"]["one_of"]) == (30, 10, 20)
    assert (skills["Java"]["required"], skills["Java"]["one_of"]) == (0, 20)


# --- merge_skills keeps the groups -----------------------------------------------------

def test_merging_skills_keeps_the_group():
    ad = make_ad(status="completed")
    old = Skill.objects.create(name="Microsoft Azure")  # an alias: merges into "Azure"
    AdSkill.objects.create(ad=ad, skill=old, level="required", alternative_group=1)
    AdSkill.objects.create(ad=ad, skill=Skill.objects.create(name="AWS"), level="required", alternative_group=1)
    call_command("merge_skills")
    assert links(ad) == {("Azure", "required", 1), ("AWS", "required", 1)}


def test_merging_keeps_the_firmer_of_two_links():
    """Azure on its own + "Microsoft Azure" in a group, same ad: on its own wins."""
    ad = make_ad(status="completed")
    AdSkill.objects.create(ad=ad, skill=Skill.objects.create(name="Azure"), level="required")
    AdSkill.objects.create(ad=ad, skill=Skill.objects.create(name="Microsoft Azure"), level="required", alternative_group=1)
    call_command("merge_skills")
    assert links(ad) == {("Azure", "required", None)}
