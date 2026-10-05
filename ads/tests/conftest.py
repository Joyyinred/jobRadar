"""
Shared test setup (pytest "fixtures"). A test asks for a fixture by naming it
as an argument: def test_x(api, enqueued): ...

Rules for every test:
  - never call the real DeepSeek (autouse fixture below forces the mock)
  - never need Redis or the worker container (enqueued / celery_eager below)
  - a separate test database (pytest-django creates test_jobradar and drops it
    afterwards — your real ads are never touched)
"""
import pytest
from rest_framework.test import APIClient

from ads import services
from config.celery import app as celery_app


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    """autouse = applies to EVERY test, without asking.

    The container's .env may say LLM_MODE=real or LLM_FAKE_DELAY=15; tests must
    be free, offline and fast whatever it says.
    """
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("LLM_FAKE_DELAY", "0")


@pytest.fixture
def api():
    """A fake browser that calls the API in-process (no server needed)."""
    return APIClient()


@pytest.fixture
def enqueued(monkeypatch):
    """Replace "put on the Redis queue" with "remember the id in a list".

    For tests about submitting: we only check THAT an ad was queued (and how
    many times), not what the worker does with it.
    """
    ids = []
    monkeypatch.setattr(services, "enqueue_parse", ids.append)
    return ids


@pytest.fixture
def celery_eager():
    """Make .delay() run the task right here, synchronously, instead of
    sending it to Redis. One test can then cover the whole pipeline:
    POST -> task -> mock LLM -> database -> GET.
    """
    celery_app.conf.task_always_eager = True
    yield
    celery_app.conf.task_always_eager = False
