"""
Fill in JobTech data for ads imported before we stored it.

    python manage.py backfill_jobtech

For every JobTech ad without an occupation group, asks the JobTech API for
the ad again (free — no LLM involved) and stores its occupation group.

An ad JobTech no longer has (404) has been taken down: its removed_at is set
to now — "seen removed at", the closest we can get to when it came down.
It stays in the database (needed for statistics); only the group stays empty.

Safe to run again: it only looks at ads still missing a group and not
already known to be removed.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from ads.adapters import JobTechClient
from ads.exceptions import NotFoundError, UpstreamError
from ads.models import JobAd


class Command(BaseCommand):
    help = "Fetch JobTech occupation groups for ads imported before they were stored."

    def handle(self, *args, **options):
        client = JobTechClient()
        todo = JobAd.objects.filter(
            source="jobtech_api", occupation_group_code="", removed_at__isnull=True,
        ).order_by("id")
        total = todo.count()
        filled = removed = errors = 0

        for n, ad in enumerate(todo, start=1):
            try:
                data = client.get_ad(ad.source_external_id)
            except NotFoundError:
                ad.removed_at = timezone.now()
                ad.save(update_fields=["removed_at"])
                removed += 1
            except UpstreamError:
                errors += 1  # JobTech hiccup: left as is, a re-run will retry it
            else:
                group = data.get("occupation_group") or {}
                ad.occupation_group_code = group.get("concept_id") or ""
                ad.occupation_group = group.get("label") or ""
                ad.save(update_fields=["occupation_group_code", "occupation_group"])
                filled += 1
            if n % 20 == 0 or n == total:
                self.stdout.write(f"{n}/{total}: {filled} filled, {removed} removed, {errors} errors")

        self.stdout.write(self.style.SUCCESS(
            f"done: {filled} filled, {removed} taken down at JobTech (removed_at set), {errors} errors"
        ))
