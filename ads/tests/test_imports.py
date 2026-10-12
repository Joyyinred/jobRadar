"""
Bulk imports in the background: the job records progress page by page and
always ends in "completed" or "failed" — never stuck in "running".
"""
import pytest

from ads import services
from ads.adapters import JobTechClient
from ads.models import ImportJob, JobAd
from ads.tests.test_adapters import FakeResponse, fake_jobtech, jobtech_ad  # noqa: F401 (fixture)
from ads.tests.test_api import assert_error
from ads.tests.test_services import LONG_AD

pytestmark = pytest.mark.django_db

SEARCH = "/api/imports/jobtech-search/"


def ads(ids):
    return [jobtech_ad(ad_id=i, text=f"{LONG_AD} {i}") for i in ids]


def test_whole_flow_post_then_poll_shows_completed_counts(api, celery_eager, fake_jobtech):  # noqa: F811
    fake_jobtech.hits = ads(range(1, 4))
    job_id = api.post(SEARCH, {"query": "java", "limit": 3}, format="json").json()["id"]

    body = api.get(f"/api/imports/{job_id}/").json()
    assert body["status"] == "completed"
    assert (body["fetched"], body["created"], body["duplicates"], body["skipped"]) == (3, 3, 0, 0)
    assert body["finished_at"] is not None
    assert JobAd.objects.count() == 3


def test_progress_is_saved_after_every_page(enqueued, monkeypatch):
    """45 ads = pages of 20, 20, 5: the job is saved three times along the way."""
    def search(self, query, limit, offset=0, occupation_field=None):
        return ads(range(offset, offset + limit))

    monkeypatch.setattr(JobTechClient, "search", search)
    seen = []
    real_save = ImportJob.save

    def recording_save(self, *args, **kwargs):
        if kwargs.get("update_fields") == ["fetched", "created", "duplicates", "skipped"]:
            seen.append((self.fetched, self.created))
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(ImportJob, "save", recording_save)
    job = ImportJob.objects.create(params={"query": "java", "limit": 45, "data_it_only": False})
    services.run_jobtech_import(job.id)

    assert seen == [(20, 20), (40, 40), (45, 45)]
    job.refresh_from_db()
    assert job.status == "completed"


def test_jobtech_down_marks_the_job_failed_with_a_message(enqueued, fake_jobtech):  # noqa: F811
    fake_jobtech.status = 503
    job = ImportJob.objects.create(params={"query": "java", "limit": 10, "data_it_only": False})
    services.run_jobtech_import(job.id)
    job.refresh_from_db()
    assert job.status == "failed"
    assert job.error == "Platsbanken returned an error. Try again later."
    assert job.finished_at is not None


def test_a_bug_marks_the_job_failed_instead_of_leaving_it_running(enqueued, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(services, "ingest_batch", broken)
    job = ImportJob.objects.create(params={"query": "java", "limit": 10, "data_it_only": False})
    services.run_jobtech_import(job.id)
    job.refresh_from_db()
    assert (job.status, job.error) == ("failed", "The import failed unexpectedly.")


def test_running_the_same_import_again_only_finds_duplicates(enqueued, fake_jobtech):  # noqa: F811
    fake_jobtech.hits = ads(range(1, 4))
    params = {"query": "java", "limit": 3, "data_it_only": False}
    first = ImportJob.objects.create(params=params)
    services.run_jobtech_import(first.id)
    again = ImportJob.objects.create(params=params)
    services.run_jobtech_import(again.id)
    again.refresh_from_db()
    assert (again.created, again.duplicates) == (0, 3)


def test_unknown_import_is_404(api):
    assert_error(api.get("/api/imports/99999/"), 404, "not_found", "IMPORT_NOT_FOUND")
