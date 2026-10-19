"""
Fill in title_en for ads parsed before it existed.

    python manage.py translate_titles --dry-run   # how many, how many calls
    python manage.py translate_titles

New ads get title_en from the parse itself (llm.SYSTEM_PROMPT). For the ones
already parsed, re-parsing just for the title would cost a whole ad's tokens
each — so this sends only the titles, BATCH of them per LLM call.

Safe to run again: it only picks ads that still have no title_en. A batch the
model answers wrongly (e.g. 39 titles back for 40) is skipped and logged, not
stored — a title next to the wrong ad is worse than no translation; the next
run tries it again.
"""
from django.core.management.base import BaseCommand

from ads import llm
from ads.models import JobAd

BATCH = 40


class Command(BaseCommand):
    help = "Translate the titles of already-parsed ads into English (title_en)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="only count, no LLM calls")

    def handle(self, *args, dry_run=False, **options):
        ads = list(JobAd.objects.exclude(title="").filter(title_en="").order_by("id"))
        batches = [ads[i:i + BATCH] for i in range(0, len(ads), BATCH)]
        self.stdout.write(f"{len(ads)} titles to translate, {len(batches)} LLM calls")
        if dry_run:
            return

        done = 0
        for batch in batches:
            try:
                english = llm.translate_titles([ad.title for ad in batch])
            except Exception as error:  # one bad batch must not stop the rest
                self.stderr.write(f"  skipped ads {batch[0].id}..{batch[-1].id}: {error}")
                continue
            for ad, title_en in zip(batch, english):
                ad.title_en = title_en.strip() or ad.title
            # One UPDATE statement for the whole batch, not one per ad.
            JobAd.objects.bulk_update(batch, ["title_en"])
            done += len(batch)
            self.stdout.write(f"  {done}/{len(ads)}")
        self.stdout.write(self.style.SUCCESS(f"translated {done} titles"))
