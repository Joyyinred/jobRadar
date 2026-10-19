"""
Merge skills that are really the same skill, in the data already stored.

    python manage.py merge_skills --dry-run   # show what would change, change nothing
    python manage.py merge_skills             # do it

Two kinds of duplicates:
  - other names from the alias table:  "Microsoft Azure" -> "Azure",
                                       "C/C++" -> "C" + "C++"   (ads/skill_aliases.py)
  - the same name in another case:     "Machine learning" -> "Machine Learning"

No LLM involved: the ads aren't parsed again, only their skill links are moved.
For an ad linked to both names, one link remains — "required" if either was.
Safe to run again: once merged, there's nothing left to merge.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from ads.models import AdSkill, Skill
from ads.skill_aliases import CANONICAL_NAMES, canonical_names, case_matters


class Command(BaseCommand):
    help = "Merge duplicate skills (alias table + case) in the stored data."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="only report what would be merged")

    def handle(self, *args, dry_run=False, **options):
        # Skills no ad uses any more (left behind when ads were parsed again —
        # save_parsed() replaces an ad's links but keeps the Skill rows).
        # Merging them would only create more empty skills: delete instead.
        unused = Skill.objects.filter(ad_skills__isnull=True)
        if dry_run:
            self.stdout.write(f"{unused.count()} skills used by no ad would be deleted")
        else:
            self.stdout.write(f"deleted {unused.delete()[0]} skills used by no ad")

        plan = self.plan()
        if not plan:
            self.stdout.write(self.style.SUCCESS("nothing to merge"))
            return

        for source, targets in plan:
            links = source.ad_skills.count()
            self.stdout.write(f"  {source.name!r} ({links} ads) -> {' + '.join(repr(t) for t in targets)}")
        if dry_run:
            self.stdout.write(self.style.WARNING(f"dry run: {len(plan)} skills would be merged, nothing changed"))
            return

        # All or nothing: a crash halfway must not leave links half-moved.
        with transaction.atomic():
            moved = sum(self.merge(source, targets) for source, targets in plan)
        self.stdout.write(self.style.SUCCESS(f"merged {len(plan)} skills, moved {moved} links"))

    def plan(self):
        """[(skill to merge away, [names it goes into]), ...]"""
        uses = dict(
            Skill.objects.annotate(n=Count("ad_skills")).filter(n__gt=0).values_list("name", "n")
        )
        plan = []
        for skill in Skill.objects.filter(name__in=uses).order_by("name"):
            targets = [self.keeper_name(name, uses) for name in canonical_names(skill.name)]
            if targets != [skill.name]:
                plan.append((skill, targets))
        return plan

    @staticmethod
    def keeper_name(name, uses):
        """Which spelling survives for this skill.

        The alias table's spelling if it has one ("Machine Learning"); else
        the spelling most ads use, preferring a capitalised one on a tie.
        """
        if case_matters(name):
            return name  # LoRA stays LoRA, LoRa stays LoRa
        spellings = [n for n in uses if n.lower() == name.lower() and not case_matters(n)]
        for canonical in CANONICAL_NAMES:
            if canonical.lower() == name.lower():
                return canonical
        if not spellings:
            return name
        return max(spellings, key=lambda n: (uses[n], n != n.lower(), n))

    @staticmethod
    def merge(source, target_names):
        """Move source's links onto the target skills, then delete source."""
        targets = [Skill.objects.get_or_create(name=name)[0] for name in target_names]
        moved = 0
        for link in list(source.ad_skills.all()):
            for target in targets:
                existing = AdSkill.objects.filter(ad_id=link.ad_id, skill=target).first()
                if existing is None:
                    AdSkill.objects.create(ad_id=link.ad_id, skill=target, level=link.level)
                elif link.level == "required" and existing.level != "required":
                    existing.level = "required"  # required beats nice-to-have
                    existing.save(update_fields=["level"])
            link.delete()
            moved += 1
        source.delete()
        return moved
