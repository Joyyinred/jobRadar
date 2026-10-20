"""
Tracking applications: start, update, history, summary — and that each user
only ever sees and changes their own.
"""
from datetime import timedelta

import pytest
from rest_framework.test import APIClient

from ads.tests.test_api import assert_error
from ads.tests.test_services import LONG_AD, make_ad
from tracking.models import Application, ApplicationEvent

pytestmark = pytest.mark.django_db


def url(ad):
    return f"/api/ads/{ad.id}/application/"


@pytest.fixture
def ad():
    return make_ad(status="completed", title="Systemutvecklare", title_en="Systems Developer")


@pytest.fixture
def other_api(django_user_model):
    """A second user, logged in: must never see or touch the first one's data."""
    client = APIClient()
    client.force_login(django_user_model.objects.create_user(username="someone-else", password="x"))
    return client


# --- Start and update ------------------------------------------------------------------

def test_not_tracking_an_ad_is_null_not_404(api, ad):
    assert api.get(url(ad)).json() == {"application": None}


def test_first_put_saves_the_ad(api, ad):
    response = api.put(url(ad), {}, format="json")
    assert response.status_code == 201
    app = response.json()["application"]
    assert (app["status"], app["ad"]["title"], app["applied_at"]) == ("saved", "Systems Developer", None)
    assert [e["status"] for e in app["events"]] == ["saved"]


def test_later_puts_update_and_log_each_status_change(api, ad):
    api.put(url(ad), {}, format="json")
    response = api.put(url(ad), {"status": "applied"}, format="json")
    assert response.status_code == 200
    api.put(url(ad), {"status": "interview", "notes": "Call with Anna on Friday"}, format="json")
    app = api.get(url(ad)).json()["application"]
    assert (app["status"], app["notes"]) == ("interview", "Call with Anna on Friday")
    assert [e["status"] for e in app["events"]] == ["saved", "applied", "interview"]
    assert app["applied_at"] == app["events"][1]["at"]


def test_same_status_again_logs_nothing(api, ad):
    """PUT is idempotent: sending the same thing twice changes nothing."""
    api.put(url(ad), {"status": "applied"}, format="json")
    api.put(url(ad), {"status": "applied"}, format="json")
    assert ApplicationEvent.objects.count() == 1


def test_notes_alone_dont_change_the_status(api, ad):
    api.put(url(ad), {"status": "applied"}, format="json")
    app = api.put(url(ad), {"notes": "Sent CV v3"}, format="json").json()["application"]
    assert (app["status"], app["notes"]) == ("applied", "Sent CV v3")
    assert ApplicationEvent.objects.count() == 1


def test_any_status_can_follow_any_other(api, ad):
    """Undoing a mistake ("rejected" -> back to "applied") is allowed and logged."""
    api.put(url(ad), {"status": "rejected"}, format="json")
    app = api.put(url(ad), {"status": "applied"}, format="json").json()["application"]
    assert [e["status"] for e in app["events"]] == ["rejected", "applied"]


def test_unknown_status_is_400(api, ad):
    assert api.put(url(ad), {"status": "ghosted"}, format="json").status_code == 400


def test_tracking_an_ad_that_doesnt_exist_is_404(api):
    assert_error(api.put("/api/ads/99999/application/", {}, format="json"), 404, "not_found", "AD_NOT_FOUND")


def test_delete_stops_tracking_and_drops_the_history(api, ad):
    api.put(url(ad), {"status": "applied"}, format="json")
    assert api.delete(url(ad)).status_code == 204
    assert api.get(url(ad)).json() == {"application": None}
    assert ApplicationEvent.objects.count() == 0
    assert api.delete(url(ad)).status_code == 204  # again: still fine, nothing left


# --- Yours only -----------------------------------------------------------------------

def test_everything_needs_a_login(anon_api, ad):
    assert anon_api.get("/api/applications/").status_code == 401
    assert anon_api.get(url(ad)).status_code == 401  # even reading: it's personal
    assert anon_api.put(url(ad), {}, format="json").status_code == 401


def test_users_see_only_their_own(api, other_api, ad):
    api.put(url(ad), {"status": "interview", "notes": "private"}, format="json")
    assert other_api.get(url(ad)).json() == {"application": None}
    assert other_api.get("/api/applications/").json()["results"] == []


def test_two_users_can_track_the_same_ad_separately(api, other_api, ad):
    api.put(url(ad), {"status": "applied"}, format="json")
    other_api.put(url(ad), {"status": "saved"}, format="json")
    assert api.get(url(ad)).json()["application"]["status"] == "applied"
    assert Application.objects.count() == 2


def test_deleting_yours_leaves_theirs(api, other_api, ad):
    api.put(url(ad), {}, format="json")
    other_api.put(url(ad), {}, format="json")
    api.delete(url(ad))
    assert other_api.get(url(ad)).json()["application"] is not None


# --- The list and the summary --------------------------------------------------------------

def make_ads(n):
    return [make_ad(raw_text=f"{LONG_AD} {i}", status="completed") for i in range(n)]


def test_list_is_newest_change_first_and_filters_by_status(api):
    a, b, c = make_ads(3)
    for ad, status in [(a, "saved"), (b, "applied"), (c, "applied")]:
        api.put(url(ad), {"status": status}, format="json")
    api.put(url(a), {"notes": "touched last"}, format="json")
    assert [r["ad"]["id"] for r in api.get("/api/applications/").json()["results"]] == [a.id, c.id, b.id]
    assert [r["ad"]["id"] for r in api.get("/api/applications/?status=applied").json()["results"]] == [c.id, b.id]


def test_summary_counts_and_response_time(api):
    a, b, c, d = make_ads(4)
    api.put(url(a), {"status": "saved"}, format="json")      # not applied
    api.put(url(b), {"status": "applied"}, format="json")    # no answer yet
    for ad in (c, d):                                        # applied, then an answer
        api.put(url(ad), {"status": "applied"}, format="json")
    api.put(url(c), {"status": "interview"}, format="json")
    api.put(url(d), {"status": "rejected"}, format="json")
    # Pretend the answers took 4 and 10 days: move the "applied" events back.
    for ad, days in ((c, 4), (d, 10)):
        event = ApplicationEvent.objects.get(application__ad=ad, status="applied")
        ApplicationEvent.objects.filter(id=event.id).update(at=event.at - timedelta(days=days))

    summary = api.get("/api/applications/").json()["summary"]
    assert summary["counts"]["applied"] == 1 and summary["counts"]["interview"] == 1
    assert (summary["applied"], summary["heard_back"]) == (3, 2)
    assert summary["days_to_answer"] == 7.0  # median of 4 and 10


def test_withdrawing_a_saved_ad_does_not_count_as_applied(api):
    [ad] = make_ads(1)
    api.put(url(ad), {"status": "withdrawn"}, format="json")
    assert api.get("/api/applications/").json()["summary"]["applied"] == 0


def test_list_shows_whether_the_ad_is_still_open(api):
    [ad] = make_ads(1)
    ad.removed_at = ad.created_at
    ad.save()
    api.put(url(ad), {}, format="json")
    assert api.get("/api/applications/").json()["results"][0]["ad"]["closed"] is True
