"""
Logging in, and what a visitor (not logged in) may and may not do.

Most tests here use a client with CSRF checks ON — like a real browser, where
the page must send the csrftoken back — not the `api` fixture, which logs in
with force_login and skips CSRF.
"""
import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from ads.tests.test_api import assert_error
from ads.tests.test_services import LONG_AD, make_ad

pytestmark = pytest.mark.django_db

PASSWORD = "a-test-password"  # the `user` fixture's (conftest.py)


@pytest.fixture(autouse=True)
def fresh_throttle():
    """The login rate limit counts attempts in the cache; start each test at 0."""
    cache.clear()


@pytest.fixture
def browser():
    """A client that, like a browser, must pass the CSRF check on every write."""
    client = APIClient(enforce_csrf_checks=True)
    client.get("/api/auth/me/")  # hands out the csrftoken cookie, like the page's first call
    client.credentials(HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
    return client


def log_in(client, username="joy", password=PASSWORD):
    response = client.post("/api/auth/login/", {"username": username, "password": password}, format="json")
    # Logging in rotates the csrftoken (so a token seen before login is useless after).
    if "csrftoken" in response.cookies:
        client.credentials(HTTP_X_CSRFTOKEN=response.cookies["csrftoken"].value)
    return response


# --- What a visitor can do ------------------------------------------------------------

def test_visitor_can_read(anon_api):
    ad = make_ad(status="completed")
    assert anon_api.get("/api/ads/").status_code == 200
    assert anon_api.get(f"/api/ads/{ad.id}/").status_code == 200
    assert anon_api.get("/api/occupation-groups/").status_code == 200


@pytest.mark.parametrize("url, body", [
    ("/api/ads/", {"raw_text": LONG_AD}),
    ("/api/imports/platsbanken/", {"url": "https://arbetsformedlingen.se/platsbanken/annonser/1"}),
    ("/api/imports/jobtech-search/", {"query": "java", "limit": 5}),
])
def test_visitor_cannot_add_ads_or_import(anon_api, url, body, enqueued, imports_queued):
    """These write, and cost LLM calls: logged-in users only."""
    response = anon_api.post(url, body, format="json")
    assert_error(response, 401, "error", "NOT_AUTHENTICATED")
    assert response["WWW-Authenticate"] == "Session"  # 401, not DRF's default 403
    assert enqueued == [] and imports_queued == []


def test_visitor_cannot_retry(anon_api, enqueued):
    ad = make_ad(status="failed")
    assert anon_api.post(f"/api/ads/{ad.id}/retry/").status_code == 401


# --- Logging in -------------------------------------------------------------------------

def test_me_says_nobody_when_not_logged_in(browser):
    assert browser.get("/api/auth/me/").json() == {"user": None}


def test_log_in_then_me_knows_you(browser, user):
    assert log_in(browser).json() == {"user": {"username": "joy"}}
    assert browser.get("/api/auth/me/").json() == {"user": {"username": "joy"}}


def test_logged_in_browser_can_add_an_ad(browser, user, enqueued):
    log_in(browser)
    assert browser.post("/api/ads/", {"raw_text": LONG_AD}, format="json").status_code == 202


def test_wrong_password_and_unknown_user_get_the_same_answer(browser, user):
    """Different messages would tell anyone which usernames exist."""
    wrong = log_in(browser, password="nope")
    unknown = log_in(browser, username="nobody")
    assert_error(wrong, 400, "validation", "BAD_LOGIN")
    assert unknown.json() == wrong.json()


def test_log_out_kills_the_session(browser, user, enqueued):
    log_in(browser)
    assert browser.post("/api/auth/logout/").json() == {"user": None}
    assert browser.get("/api/auth/me/").json() == {"user": None}
    assert browser.post("/api/ads/", {"raw_text": LONG_AD}, format="json").status_code == 401


def test_too_many_login_attempts_are_refused(browser, user):
    for _ in range(5):
        log_in(browser, password="guess")
    assert log_in(browser).status_code == 429  # even the right password, for now


# --- CSRF: another site can't use your cookie ---------------------------------------------

def test_write_without_the_csrf_token_is_refused(user, enqueued):
    """Logged in (cookie sent), but no X-CSRFToken: what a form on another
    site would send. Refused, nothing queued."""
    client = APIClient(enforce_csrf_checks=True)
    client.get("/api/auth/me/")
    client.credentials(HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
    log_in(client)
    client.credentials()  # drop the header; the cookies stay
    response = client.post("/api/ads/", {"raw_text": LONG_AD}, format="json")
    assert response.status_code == 403
    assert "CSRF" in response.json()["message"]
    assert enqueued == []


def test_login_itself_needs_the_csrf_token(user):
    """Else another site could log your browser into ITS account ("login CSRF")."""
    client = APIClient(enforce_csrf_checks=True)
    assert log_in(client).status_code == 403
