"""
End-to-end integration tests: the whole pipeline in one test.

    POST /api/ads/ -> services -> Celery task -> (mock) LLM -> 4 tables -> GET

`celery_eager` makes .delay() run the task right away in this process, so no
Redis or worker container is needed. The LLM is always the mock (conftest).
"""
import pytest

from ads.models import AdSkill, Company, JobAd
from ads.tests.test_services import LONG_AD

pytestmark = pytest.mark.django_db

ADS = "/api/ads/"


def test_post_then_get_shows_the_parsed_ad(api, celery_eager):
    ad_id = api.post(ADS, {"raw_text": LONG_AD}, format="json").json()["id"]

    body = api.get(f"{ADS}{ad_id}/").json()

    assert body["status"] == "completed"
    # The mock LLM's fixed answer, gathered back from the four tables:
    assert body["result"] == {
        "title": "Mock Junior Backend Developer",
        "company": "Mock Company AB",
        "city": "Stockholm",
        "seniority": "junior",
        "required_skills": ["Python", "Django", "PostgreSQL"],
        "nice_to_have_skills": ["Docker", "AWS"],
        "swedish_requirement": "preferred",
    }
    assert Company.objects.filter(name="Mock Company AB").count() == 1
    assert AdSkill.objects.filter(ad_id=ad_id).count() == 5


def test_llm_failure_ends_as_failed_after_retries(api, celery_eager):
    """MOCK_FAIL makes the mock LLM raise every time: the task retries
    (3 times), then marks the ad failed with a safe message."""
    ad_id = api.post(ADS, {"raw_text": LONG_AD + " MOCK_FAIL"}, format="json").json()["id"]

    assert api.get(f"{ADS}{ad_id}/").json() == {
        "id": ad_id,
        "status": "failed",
        "error": "Could not parse this ad. Please try again.",
    }


def test_failed_ad_can_be_retried_and_then_completes(api, celery_eager, monkeypatch):
    ad_id = api.post(ADS, {"raw_text": LONG_AD + " MOCK_FAIL"}, format="json").json()["id"]
    assert JobAd.objects.get(id=ad_id).status == "failed"

    # The LLM recovers (here: the mock stops failing), the user clicks Retry.
    monkeypatch.setattr("ads.llm.mock_llm", lambda raw_text: {
        "title": "Recovered", "company": "", "city": "", "seniority": "junior",
        "required_skills": ["Go"], "nice_to_have_skills": [], "swedish_requirement": "not_mentioned",
    })
    assert api.post(f"{ADS}{ad_id}/retry/").status_code == 202

    body = api.get(f"{ADS}{ad_id}/").json()
    assert body["status"] == "completed"
    assert body["result"]["title"] == "Recovered"
    assert body["result"]["required_skills"] == ["Go"]
