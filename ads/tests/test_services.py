"""
Unit tests for services.py: duplicate detection, the short-ad warning, retry,
and save_parsed. These use the test database, but no HTTP and no queue
(the `enqueued` fixture records ids instead of sending them to Redis).
"""
import pytest

from ads import services
from ads.exceptions import BlockError, NotFoundError, WarningException
from ads.models import AdSkill, Company, JobAd

pytestmark = pytest.mark.django_db  # every test in this file may use the database

LONG_AD = (
    "Junior Backend Developer at Testbolaget AB in Uppsala. You will build REST "
    "APIs in Python and Django, work with PostgreSQL and Docker, and join a "
    "friendly team. Meriterande: AWS and Kubernetes. Svenska krävs."
)
assert len(LONG_AD) >= services.SHORT_AD_CHARS  # guard: the fixture itself must be "long"


def make_ad(**fields):
    """An ad row straight in the database, bypassing submit_ad."""
    raw_text = fields.pop("raw_text", LONG_AD)
    return JobAd.objects.create(raw_text=raw_text, content_hash=services.content_hash_of(raw_text), **fields)


# --- submit_ad: new ads --------------------------------------------------------

def test_new_ad_is_saved_pending_and_queued_once(enqueued):
    ad, created = services.submit_ad(LONG_AD)
    assert created
    assert ad.status == "pending"
    assert ad.content_hash == services.content_hash_of(LONG_AD)
    assert enqueued == [ad.id]


def test_raw_text_is_stored_unchanged(enqueued):
    text = "  " + LONG_AD + "\n\n"
    ad, _ = services.submit_ad(text)
    ad.refresh_from_db()
    assert ad.raw_text == text


# --- submit_ad: exact duplicates ----------------------------------------------

def test_same_text_returns_existing_ad_and_queues_nothing(enqueued):
    first, _ = services.submit_ad(LONG_AD)
    enqueued.clear()

    again, created = services.submit_ad(LONG_AD)

    assert not created
    assert again.id == first.id
    assert enqueued == []  # no second LLM call
    assert JobAd.objects.count() == 1


def test_same_text_with_different_whitespace_is_a_duplicate(enqueued):
    first, _ = services.submit_ad(LONG_AD)
    again, created = services.submit_ad("   " + LONG_AD.replace(" ", "  ") + "\r\n")
    assert not created
    assert again.id == first.id


def test_same_url_different_text_is_a_duplicate(enqueued):
    url = "https://example.com/jobs/42"
    first, _ = services.submit_ad(LONG_AD, source_url=url)
    again, created = services.submit_ad(LONG_AD + " (edited)", source_url=url)
    assert not created
    assert again.id == first.id


def test_different_text_no_url_is_a_new_ad(enqueued):
    services.submit_ad(LONG_AD)
    _, created = services.submit_ad(LONG_AD + " Another role.")
    assert created
    assert JobAd.objects.count() == 2


def test_database_constraint_catches_a_duplicate_the_first_check_missed(enqueued, monkeypatch):
    """The race: two identical submits both pass the Python check before
    either INSERT. Simulated by making the FIRST lookup see nothing; the
    unique constraint must then reject the INSERT, and submit_ad must return
    the ad that won instead of crashing."""
    winner = make_ad()

    real_filter = JobAd.objects.filter
    calls = {"n": 0}

    def filter_that_misses_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return JobAd.objects.none()  # "nothing there yet" — the race
        return real_filter(*args, **kwargs)

    monkeypatch.setattr(JobAd.objects, "filter", filter_that_misses_once)

    ad, created = services.submit_ad(LONG_AD)

    assert not created
    assert ad.id == winner.id
    assert enqueued == []
    assert JobAd.objects.count() == 1


# --- submit_ad: the short-ad warning ------------------------------------------

def test_short_ad_raises_warning_and_saves_nothing(enqueued):
    with pytest.raises(WarningException) as info:
        services.submit_ad("Java dev wanted")
    assert info.value.code == "AD_TOO_SHORT"
    assert info.value.detail == {"chars": 15, "min_chars": services.SHORT_AD_CHARS}
    assert JobAd.objects.count() == 0
    assert enqueued == []


def test_short_ad_with_confirm_is_saved(enqueued):
    ad, created = services.submit_ad("Java dev wanted", confirm=True)
    assert created
    assert enqueued == [ad.id]


def test_short_duplicate_is_returned_without_a_warning(enqueued):
    # Duplicate check comes first: no point warning about an ad we already have.
    first, _ = services.submit_ad("Java dev wanted", confirm=True)
    again, created = services.submit_ad("Java dev wanted")  # no confirm
    assert not created
    assert again.id == first.id


# --- get_ad / retry_ad --------------------------------------------------------

def test_get_missing_ad_raises_not_found():
    with pytest.raises(NotFoundError) as info:
        services.get_ad(99999)
    assert info.value.code == "AD_NOT_FOUND"


def test_retry_failed_ad_makes_it_pending_and_queues_it(enqueued):
    ad = make_ad(status="failed", error="Could not parse this ad. Please try again.")
    retried = services.retry_ad(ad.id)
    assert retried.status == "pending"
    assert retried.error == ""
    assert enqueued == [ad.id]


@pytest.mark.parametrize("status", ["pending", "processing", "completed"])
def test_retry_non_failed_ad_is_blocked(enqueued, status):
    ad = make_ad(status=status)
    with pytest.raises(BlockError) as info:
        services.retry_ad(ad.id)
    assert info.value.code == "AD_NOT_RETRYABLE"
    assert info.value.detail["status"] == status
    assert enqueued == []


def test_retry_missing_ad_raises_not_found(enqueued):
    with pytest.raises(NotFoundError):
        services.retry_ad(99999)


def test_retry_twice_queues_once(enqueued):
    ad = make_ad(status="failed")
    services.retry_ad(ad.id)
    with pytest.raises(BlockError):
        services.retry_ad(ad.id)  # now "pending", not "failed"
    assert enqueued == [ad.id]


# --- save_parsed --------------------------------------------------------------

PARSED = {
    "title": "Junior Backend Developer",
    "company": "Testbolaget AB",
    "city": "Uppsala",
    "seniority": "junior",
    "required_skills": ["Python", "Django"],
    "nice_to_have_skills": ["AWS"],
    "swedish_requirement": "required",
}


def skills_of(ad):
    return {(s.skill.name, s.level) for s in ad.ad_skills.select_related("skill")}


def test_save_parsed_fills_columns_and_skill_links():
    ad = make_ad()
    services.save_parsed(ad, PARSED)
    ad.refresh_from_db()
    assert ad.title == "Junior Backend Developer"
    assert ad.company.name == "Testbolaget AB"
    assert ad.city == "Uppsala"
    assert skills_of(ad) == {("Python", "required"), ("Django", "required"), ("AWS", "nice_to_have")}


def test_two_ads_from_one_company_share_one_company_row():
    ad1 = make_ad()
    ad2 = make_ad(raw_text=LONG_AD + " Second role.")
    services.save_parsed(ad1, PARSED)
    services.save_parsed(ad2, PARSED)
    assert Company.objects.count() == 1


def test_unspecified_company_is_left_empty():
    ad = make_ad()
    services.save_parsed(ad, {**PARSED, "company": "unspecified"})
    assert ad.company is None


def test_skill_listed_twice_is_stored_once():
    ad = make_ad()
    services.save_parsed(ad, {**PARSED, "required_skills": ["Python", "Python"], "nice_to_have_skills": []})
    assert AdSkill.objects.filter(ad=ad).count() == 1


def test_saving_again_replaces_old_skills_instead_of_mixing():
    ad = make_ad()
    services.save_parsed(ad, PARSED)
    services.save_parsed(ad, {**PARSED, "required_skills": ["Go"], "nice_to_have_skills": []})
    assert skills_of(ad) == {("Go", "required")}
