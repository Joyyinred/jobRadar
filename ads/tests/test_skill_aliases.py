"""
One name per skill: the alias table, case-insensitive reuse, and the
merge_skills command that cleans up what's already stored.
"""
from io import StringIO

import pytest
from django.core.management import call_command

from ads import services
from ads.models import AdSkill, JobAd, Skill
from ads.services import content_hash_of
from ads.skill_aliases import canonical_names

# --- the alias table (no database) ------------------------------------------------


@pytest.mark.parametrize("name, expected", [
    ("Microsoft Azure", ["Azure"]),
    ("microsoft   azure", ["Azure"]),        # case and extra spaces ignored
    ("Google Cloud Platform", ["GCP"]),
    (".NET Core", [".NET"]),
    ("Azure", ["Azure"]),                    # a canonical name maps to itself
    ("C/C++", ["C", "C++"]),                 # one name, two skills
    ("  Some  Niche   Tool ", ["Some Niche Tool"]),  # unknown: trimmed, otherwise as is
])
def test_canonical_names(name, expected):
    assert canonical_names(name) == expected


# --- saving parsed ads ------------------------------------------------------------

pytestmark_db = pytest.mark.django_db


def new_ad(n=1):
    text = f"alias test ad {n}"
    return JobAd.objects.create(raw_text=text, content_hash=content_hash_of(text), status="completed")


def skills_of(ad):
    return {(s.skill.name, s.level) for s in ad.ad_skills.select_related("skill")}


def parsed(required=(), nice=()):
    return {"title": "t", "required_skills": list(required), "nice_to_have_skills": list(nice)}


@pytestmark_db
def test_two_names_for_one_skill_become_one_link():
    ad = new_ad()
    services.save_parsed(ad, parsed(required=["Microsoft Azure", "Azure"]))
    assert skills_of(ad) == {("Azure", "required")}


@pytestmark_db
def test_required_wins_when_a_skill_is_in_both_lists():
    ad = new_ad()
    services.save_parsed(ad, parsed(required=["Azure"], nice=["Microsoft Azure"]))
    assert skills_of(ad) == {("Azure", "required")}


@pytestmark_db
def test_c_slash_cpp_is_two_skills():
    ad = new_ad()
    services.save_parsed(ad, parsed(required=["C/C++"]))
    assert skills_of(ad) == {("C", "required"), ("C++", "required")}


@pytestmark_db
def test_a_new_capitalisation_reuses_the_existing_skill():
    Skill.objects.create(name="Cloud")
    ad = new_ad()
    services.save_parsed(ad, parsed(required=["cloud"]))
    assert skills_of(ad) == {("Cloud", "required")}
    assert Skill.objects.filter(name__iexact="cloud").count() == 1


@pytestmark_db
def test_lora_and_lora_stay_two_skills():
    """LoRA (LLM fine-tuning) and LoRa (IoT radio): same letters, different things."""
    Skill.objects.create(name="LoRa")
    ad = new_ad()
    services.save_parsed(ad, parsed(required=["LoRA"]))
    assert skills_of(ad) == {("LoRA", "required")}
    assert set(Skill.objects.filter(name__iexact="lora").values_list("name", flat=True)) == {"LoRa", "LoRA"}


# --- merge_skills on stored data -------------------------------------------------

def link(ad, name, level="required"):
    skill, _ = Skill.objects.get_or_create(name=name)
    return AdSkill.objects.create(ad=ad, skill=skill, level=level)


def run_merge(*args):
    out = StringIO()
    call_command("merge_skills", *args, stdout=out)
    return out.getvalue()


@pytestmark_db
def test_dry_run_changes_nothing():
    ad = new_ad()
    link(ad, "Microsoft Azure")
    Skill.objects.create(name="Unused")
    output = run_merge("--dry-run")
    assert "'Microsoft Azure' (1 ads) -> 'Azure'" in output
    assert Skill.objects.filter(name="Microsoft Azure").exists()
    assert Skill.objects.filter(name="Unused").exists()


@pytestmark_db
def test_merge_moves_links_and_deletes_the_old_names():
    a1, a2 = new_ad(1), new_ad(2)
    link(a1, "Microsoft Azure")
    link(a2, "Azure", "nice_to_have")
    link(a2, "C/C++")
    run_merge()
    assert skills_of(a1) == {("Azure", "required")}
    assert skills_of(a2) == {("Azure", "nice_to_have"), ("C", "required"), ("C++", "required")}
    assert not Skill.objects.filter(name__in=["Microsoft Azure", "C/C++"]).exists()


@pytestmark_db
def test_an_ad_with_both_names_keeps_one_link_and_required_wins():
    ad = new_ad()
    link(ad, "Azure", "nice_to_have")
    link(ad, "Microsoft Azure", "required")
    run_merge()
    assert skills_of(ad) == {("Azure", "required")}


@pytestmark_db
def test_case_variants_merge_into_the_most_used_spelling():
    for n in range(3):
        link(new_ad(n), "Change management")
    link(new_ad(9), "Change Management")
    run_merge()
    assert list(Skill.objects.filter(name__iexact="change management").values_list("name", flat=True)) == [
        "Change management"
    ]
    assert AdSkill.objects.filter(skill__name="Change management").count() == 4


@pytestmark_db
def test_unused_skills_are_deleted_and_lora_lora_left_alone():
    Skill.objects.create(name="felsökning")  # left over from before a reparse
    link(new_ad(1), "LoRA")
    link(new_ad(2), "LoRa")
    run_merge()
    assert not Skill.objects.filter(name="felsökning").exists()
    assert set(Skill.objects.values_list("name", flat=True)) == {"LoRA", "LoRa"}


@pytestmark_db
def test_running_it_again_finds_nothing_to_merge():
    link(new_ad(), "Microsoft Azure")
    run_merge()
    assert "nothing to merge" in run_merge()
