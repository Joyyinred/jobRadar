"""
Refresh what we store from JobTech, for ads imported before we stored it.

    python manage.py backfill_jobtech

For every JobTech ad not known to be removed, asks the JobTech API for the ad
again (free — no LLM involved) and updates the fields that come straight from
JobTech: occupation group, how to apply, and the deadline. Added when the
occupation group was new; reused when the application link was new — each
time a field is added, one re-run fills it in for the old ads.

The fields are read with JobTechClient.to_raw(), the same code an import
uses, so a backfilled ad looks exactly like a freshly imported one.

An ad JobTech no longer has (404) has been taken down: its removed_at is set
to now — "seen removed at", the closest we can get to when it came down.
It stays in the database (needed for statistics).

Safe to run again: it just fetches and writes the same values again.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from ads.adapters import JobTechClient
from ads.exceptions import NotFoundError, UpstreamError
from ads.models import JobAd

# The JobAd fields that are a straight copy of JobTech data (RawJobAd has the
# same names). raw_text is NOT here: it's the source of truth for the parse
# and is never rewritten.
JOBTECH_FIELDS = [
    "expires_at", "occupation_group_code", "occupation_group", "apply_url", "apply_email",
]


class Command(BaseCommand):
    help = "Re-fetch JobTech ads and update their JobTech fields (group, apply link, deadline)."

    def handle(self, *args, **options):
        client = JobTechClient()
        todo = JobAd.objects.filter(source="jobtech_api", removed_at__isnull=True).order_by("id")
        total = todo.count()
        updated = removed = errors = 0

        for n, ad in enumerate(todo, start=1):
            try:
                raw = client.to_raw(client.get_ad(ad.source_external_id))
            except NotFoundError:
                ad.removed_at = timezone.now()
                ad.save(update_fields=["removed_at"])
                removed += 1
            except UpstreamError:
                errors += 1  # JobTech hiccup: left as is, a re-run will retry it
            else:
                for field in JOBTECH_FIELDS:
                    setattr(ad, field, getattr(raw, field))
                ad.save(update_fields=JOBTECH_FIELDS)
                updated += 1
            if n % 20 == 0 or n == total:
                self.stdout.write(f"{n}/{total}: {updated} updated, {removed} removed, {errors} errors")

        self.stdout.write(self.style.SUCCESS(
            f"done: {updated} updated, {removed} taken down at JobTech (removed_at set), {errors} errors"
        ))
