"""
Day 11: the metrics count what they claim to count.

A wrong metric fails silently — the dashboard just shows a wrong number —
so each one is checked here. Counters are global to the process and other
tests also bump them, so every test compares before/after (delta), never an
absolute value.
"""
import pytest
from prometheus_client import REGISTRY

from ads import services
from ads.adapters import JobTechClient, PasteAdapter
from ads.exceptions import UpstreamError, WarningException
from ads.tests.test_adapters import FakeResponse, fake_jobtech, jobtech_ad  # noqa: F401 (fixture)
from ads.tests.test_services import LONG_AD

pytestmark = pytest.mark.django_db


def value(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


class Delta:
    """with Delta("metric_total", label=...) as d: ...  then d.value"""

    def __init__(self, name, **labels):
        self.name, self.labels = name, labels

    def __enter__(self):
        self.before = value(self.name, **self.labels)
        return self

    def __exit__(self, *exc):
        self.value = value(self.name, **self.labels) - self.before


# --- ingest ---------------------------------------------------------------------

def test_new_and_duplicate_ads_are_counted_by_source(enqueued):
    with Delta("jobradar_ingest_total", source="manual_paste", result="created") as created, \
         Delta("jobradar_ingest_total", source="manual_paste", result="duplicate") as duplicate:
        services.submit_ad(LONG_AD)
        services.submit_ad(LONG_AD)
    assert (created.value, duplicate.value) == (1, 1)


def test_warning_and_invalid_are_counted(enqueued):
    with Delta("jobradar_ingest_total", source="manual_paste", result="warning") as warning, \
         Delta("jobradar_ingest_total", source="manual_paste", result="invalid") as invalid:
        with pytest.raises(WarningException):
            services.submit_ad("Short.")
        with pytest.raises(Exception):
            services.ingest(PasteAdapter("   ").fetch()[0])
    assert (warning.value, invalid.value) == (1, 1)


# --- the pipeline (worker side, run eagerly) -------------------------------------

def test_completed_parse_counts_llm_call_and_time_to_parsed(api, celery_eager):
    with Delta("jobradar_parse_total", result="completed") as completed, \
         Delta("jobradar_llm_requests_total", mode="mock", result="success") as llm_ok, \
         Delta("jobradar_llm_request_duration_seconds_count", mode="mock") as timed, \
         Delta("jobradar_ad_time_to_parsed_seconds_count") as waited:
        api.post("/api/ads/", {"raw_text": LONG_AD}, format="json")
    assert (completed.value, llm_ok.value, timed.value, waited.value) == (1, 1, 1, 1)


def test_failing_llm_counts_retries_then_one_failure(api, celery_eager):
    with Delta("jobradar_parse_total", result="retried") as retried, \
         Delta("jobradar_parse_total", result="failed") as failed, \
         Delta("jobradar_llm_requests_total", mode="mock", result="failure") as llm_failed:
        api.post("/api/ads/", {"raw_text": LONG_AD + " MOCK_FAIL"}, format="json")
    # 4 attempts: 3 retried, then failed for good.
    assert (retried.value, failed.value, llm_failed.value) == (3, 1, 4)


def test_real_llm_call_records_tokens(monkeypatch):
    """Tokens come from DeepSeek's reply; checked with a fake Anthropic client."""
    from types import SimpleNamespace

    from ads import llm

    reply = SimpleNamespace(
        content=[SimpleNamespace(type="text", text='{"title": "x"}')],
        usage=SimpleNamespace(input_tokens=1200, output_tokens=150),
        stop_reason="end_turn",
    )
    fake_client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: reply))
    monkeypatch.setenv("LLM_MODE", "real")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm.anthropic, "Anthropic", lambda **kwargs: fake_client)

    with Delta("jobradar_llm_tokens_total", kind="input") as tokens_in, \
         Delta("jobradar_llm_tokens_total", kind="output") as tokens_out:
        assert llm.call_llm("an ad") == {"title": "x"}
    assert (tokens_in.value, tokens_out.value) == (1200, 150)


# --- JobTech --------------------------------------------------------------------

def test_jobtech_calls_are_counted_by_result(fake_jobtech):  # noqa: F811 (fixture)
    fake_jobtech.ads = {"1": jobtech_ad(ad_id=1)}
    client = JobTechClient()
    with Delta("jobradar_jobtech_requests_total", result="ok") as ok, \
         Delta("jobradar_jobtech_requests_total", result="not_found") as not_found, \
         Delta("jobradar_jobtech_requests_total", result="error") as error:
        client.get_ad("1")
        with pytest.raises(Exception):
            client.get_ad("2")
        fake_jobtech.status = 500
        with pytest.raises(UpstreamError):
            client.get_ad("1")
    assert (ok.value, not_found.value, error.value) == (1, 1, 1)


# --- the /metrics page ----------------------------------------------------------

def test_metrics_page_lists_our_metrics_and_http_metrics(api):
    api.get("/api/ads/")
    body = api.get("/metrics").content.decode()
    assert "jobradar_queue_depth" in body
    assert "django_http_responses_total_by_status_view_method_total" in body


def test_every_known_series_exists_at_zero_before_use():
    """Without this, the first increment is invisible to rate()/increase()
    and the dashboard shows empty panels (seen on 2026-10-09)."""
    for name, labels in [
        ("jobradar_parse_total", {"result": "failed"}),
        ("jobradar_ingest_total", {"source": "jobtech_api", "result": "invalid"}),
        ("jobradar_llm_tokens_total", {"kind": "output"}),
        ("jobradar_jobtech_requests_total", {"result": "error"}),
    ]:
        assert REGISTRY.get_sample_value(name, labels) is not None, name
