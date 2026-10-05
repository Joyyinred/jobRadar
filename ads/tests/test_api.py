"""
Integration tests for the HTTP API: a request goes through the real
urls -> view -> serializer -> service -> database -> exception handler.
Only the Redis queue is replaced (the `enqueued` fixture).

The point of the error tests: EVERY error has the same shape, so the
frontend can handle all of them with one branch.
"""
import pytest

from ads import services
from ads.models import JobAd
from ads.tests.test_services import LONG_AD, make_ad

pytestmark = pytest.mark.django_db

ADS = "/api/ads/"


def assert_error(response, status, type_, code):
    """The one error format: {type, code, message, detail} — nothing else."""
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"type", "code", "message", "detail"}
    assert body["type"] == type_
    assert body["code"] == code
    assert isinstance(body["message"], str) and body["message"]
    assert isinstance(body["detail"], dict)
    return body


# --- success paths ------------------------------------------------------------

def test_post_new_ad_returns_202_pending(api, enqueued):
    response = api.post(ADS, {"raw_text": LONG_AD}, format="json")
    assert response.status_code == 202
    body = response.json()
    assert body == {"id": body["id"], "status": "pending"}
    assert enqueued == [body["id"]]


def test_post_duplicate_returns_200_with_the_existing_ad(api, enqueued):
    first = api.post(ADS, {"raw_text": LONG_AD}, format="json").json()
    response = api.post(ADS, {"raw_text": LONG_AD}, format="json")
    assert response.status_code == 200  # not 202: nothing new started
    assert response.json() == {"id": first["id"], "status": "pending", "duplicate": True}
    assert len(enqueued) == 1


def test_list_returns_summaries_newest_first(api, enqueued):
    old, _ = services.submit_ad(LONG_AD)
    new, _ = services.submit_ad(LONG_AD + " Second.")
    body = api.get(ADS).json()
    assert [a["id"] for a in body] == [new.id, old.id]
    assert body[0] == {"id": new.id, "status": "pending", "title": None, "company": None}


def test_get_completed_ad_includes_result(api):
    ad = make_ad(status="completed", title="Dev", city="Lund", seniority="junior", swedish_requirement="required")
    body = api.get(f"{ADS}{ad.id}/").json()
    assert body["status"] == "completed"
    assert body["result"]["title"] == "Dev"
    assert list(body["result"]) == [
        "title", "company", "city", "seniority",
        "required_skills", "nice_to_have_skills", "swedish_requirement",
    ]  # the order the page's table relies on


def test_get_failed_ad_includes_its_error(api):
    ad = make_ad(status="failed", error="Could not parse this ad. Please try again.")
    assert api.get(f"{ADS}{ad.id}/").json() == {
        "id": ad.id, "status": "failed", "error": "Could not parse this ad. Please try again.",
    }


def test_retry_failed_ad_returns_202(api, enqueued):
    ad = make_ad(status="failed")
    response = api.post(f"{ADS}{ad.id}/retry/")
    assert response.status_code == 202
    assert response.json() == {"id": ad.id, "status": "pending"}
    assert enqueued == [ad.id]


# --- every error, one format --------------------------------------------------

def test_missing_raw_text_is_400_validation(api):
    body = assert_error(api.post(ADS, {}, format="json"), 400, "validation", "INVALID_INPUT")
    assert "raw_text" in body["detail"]["fields"]


def test_two_bad_fields_are_both_reported(api):
    body = assert_error(
        api.post(ADS, {"raw_text": "  ", "source_url": "nope"}, format="json"),
        400, "validation", "INVALID_INPUT",
    )
    assert set(body["detail"]["fields"]) == {"raw_text", "source_url"}


def test_too_long_ad_is_400_and_not_saved(api):
    assert_error(api.post(ADS, {"raw_text": "a" * 50_001}, format="json"), 400, "validation", "INVALID_INPUT")
    assert JobAd.objects.count() == 0


def test_malformed_json_is_400_in_the_same_format(api):
    response = api.post(ADS, '{"raw_text": ', content_type="application/json")
    assert_error(response, 400, "error", "PARSE_ERROR")


def test_short_ad_is_409_warning_and_not_saved(api, enqueued):
    body = assert_error(api.post(ADS, {"raw_text": "Java dev"}, format="json"), 409, "warning", "AD_TOO_SHORT")
    assert body["detail"]["min_chars"] == services.SHORT_AD_CHARS
    assert JobAd.objects.count() == 0


def test_short_ad_with_confirm_is_202(api, enqueued):
    response = api.post(ADS, {"raw_text": "Java dev", "confirm": True}, format="json")
    assert response.status_code == 202


def test_get_missing_ad_is_404(api):
    assert_error(api.get(f"{ADS}99999/"), 404, "not_found", "AD_NOT_FOUND")


def test_retry_missing_ad_is_404(api):
    assert_error(api.post(f"{ADS}99999/retry/"), 404, "not_found", "AD_NOT_FOUND")


def test_retry_completed_ad_is_409_block(api, enqueued):
    ad = make_ad(status="completed")
    body = assert_error(api.post(f"{ADS}{ad.id}/retry/"), 409, "block", "AD_NOT_RETRYABLE")
    assert body["detail"] == {"id": ad.id, "status": "completed"}
    assert enqueued == []


def test_wrong_method_is_405_in_the_same_format(api):
    assert_error(api.put(ADS, {}), 405, "error", "METHOD_NOT_ALLOWED")


def test_unexpected_bug_is_500_without_a_stack_trace(api, monkeypatch):
    """A crash we didn't plan for: the user gets a safe message, never the
    traceback (which would leak code paths and data)."""
    def explode(ad_id):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(services, "get_ad", explode)
    response = api.get(f"{ADS}1/")
    body = assert_error(response, 500, "error", "INTERNAL_ERROR")
    assert "secret internal detail" not in response.content.decode()
    assert "Traceback" not in response.content.decode()
    assert body["detail"] == {}
