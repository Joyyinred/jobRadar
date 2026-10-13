"""
Parse ads again with the current prompt.

    python manage.py reparse            # every ad
    python manage.py reparse --id 12    # one ad (repeatable: --id 12 --id 15)

Why this works at all: the raw text was stored untouched (Day 2's "raw is the
source of truth"), so a better prompt can be applied to every ad collected so
far. Each ad goes back to "pending" and onto the queue; the worker parses it
and save_parsed() replaces its old fields and skill links.

Costs one LLM call per ad when LLM_MODE=real.
"""
from django.core.management.base import BaseCommand

from ads import services
from ads.models import JobAd


class Command(BaseCommand):
    help = "Queue ads to be parsed again with the current prompt."

    def add_arguments(self, parser):
        parser.add_argument("--id", type=int, action="append", dest="ids", help="only this ad (repeatable)")

    def handle(self, *args, ids=None, **options):
        ads = JobAd.objects.order_by("id")
        if ids:
            ads = ads.filter(id__in=ids)
        count = 0
        for ad in ads:
            ad.status, ad.error = "pending", ""
            ad.save(update_fields=["status", "error"])
            services.enqueue_parse(ad.id)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"queued {count} ads for parsing"))
