"""
Parse ads again with the current prompt.

    python manage.py reparse            # every ad
    python manage.py reparse --id 12    # one ad (repeatable: --id 12 --id 15)
    python manage.py reparse --students # thesis, internship and intern-level ads
    python manage.py reparse --missing-degree-level  # parsed before degree_level existed

Why this works at all: the raw text was stored untouched (Day 2's "raw is the
source of truth"), so a better prompt can be applied to every ad collected so
far. Each ad goes back to "pending" and onto the queue; the worker parses it
and save_parsed() replaces its old fields and skill links.

Costs one LLM call per ad when LLM_MODE=real.
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

from ads import services
from ads.models import JobAd


class Command(BaseCommand):
    help = "Queue ads to be parsed again with the current prompt."

    def add_arguments(self, parser):
        parser.add_argument("--id", type=int, action="append", dest="ids", help="only this ad (repeatable)")
        # Added with degree_level: it matters most for these, so re-parse just
        # them (about 20 LLM calls) instead of every ad.
        parser.add_argument("--students", action="store_true", help="only thesis, internship and intern-level ads")
        # "" = never asked (parsed before the field existed); "unspecified" =
        # asked, and the ad doesn't say. Only the first needs the LLM again —
        # and once parsed, an ad drops out: running it twice costs nothing.
        parser.add_argument("--missing-degree-level", action="store_true",
                            help="only parsed ads that have no degree_level yet")

    def handle(self, *args, ids=None, students=False, missing_degree_level=False, **options):
        ads = JobAd.objects.order_by("id")
        if ids:
            ads = ads.filter(id__in=ids)
        if students:
            ads = ads.filter(Q(employment_type__in=["thesis", "internship"]) | Q(seniority="intern"))
        if missing_degree_level:
            ads = ads.filter(status="completed", degree_level="")
        count = 0
        for ad in ads:
            ad.status, ad.error = "pending", ""
            ad.save(update_fields=["status", "error"])
            services.enqueue_parse(ad.id)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"queued {count} ads for parsing"))
