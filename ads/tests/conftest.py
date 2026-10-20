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


@pytest.fixture(autouse=True)
def fast_password_hashing(settings):
    """Real password hashing (PBKDF2, ~1,000,000 rounds) is slow ON PURPOSE —
    it makes guessing stolen hashes expensive — and every test makes a user.
    Tests don't need that protection: MD5 makes the suite fast again.
    Never in settings.py."""
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture
def user(db, django_user_model):
    return django_user_model.objects.create_user(username="joy", password="a-test-password")


@pytest.fixture
def api(user):
    """A fake browser that calls the API in-process (no server needed),
    logged in as `user` — most tests are about what a user can do.
    (force_login skips the password check and, like APIClient by default,
    the CSRF check: test_auth.py tests those for real.)"""
    client = APIClient()
    client.force_login(user)
    return client


@pytest.fixture
def anon_api():
    """The same, not logged in: a visitor."""
    return APIClient()


@pytest.fixture(autouse=True)
def no_real_queue(request, monkeypatch):
    """EVERY test: nothing ever reaches the real Redis queue.

    Without this, a test that doesn't think about the queue would put real
    tasks on it — and the real worker (if docker compose is up) would run them
    against the real database. Queued ids are recorded in lists instead.

    The exception is a test asking for `celery_eager`: there the tasks are
    meant to run, right here, in-process.
    """
    queued = {"parse": [], "import": []}
    if "celery_eager" not in request.fixturenames:
        monkeypatch.setattr(services, "enqueue_parse", queued["parse"].append)
        monkeypatch.setattr(services, "enqueue_import", queued["import"].append)
    return queued


@pytest.fixture
def enqueued(no_real_queue):
    """The ad ids "queued for parsing" during the test (see no_real_queue).

    For tests about submitting: we only check THAT an ad was queued (and how
    many times), not what the worker does with it.
    """
    return no_real_queue["parse"]


@pytest.fixture
def imports_queued(no_real_queue):
    """The ImportJob ids "queued" during the test."""
    return no_real_queue["import"]


@pytest.fixture
def celery_eager():
    """Make .delay() run the task right here, synchronously, instead of
    sending it to Redis. One test can then cover the whole pipeline:
    POST -> task -> mock LLM -> database -> GET.
    """
    celery_app.conf.task_always_eager = True
    yield
    celery_app.conf.task_always_eager = False
