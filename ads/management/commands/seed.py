"""
Load fictional mock job ads straight into the database — no LLM calls.

    python manage.py seed           # add 40 ads, keep what's there
    python manage.py seed --flush   # wipe the four ads tables first
    python manage.py seed --count 100

All companies are invented. Each ad is built from a role template (which
skills are required / nice-to-have), then a readable raw_text is written from
the same fields, so raw_text and the parsed columns always agree.

Fixed random seed: running it twice on an empty database gives the same data.
"""
import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from ads.models import AdSkill, Company, JobAd, Skill
from ads.services import content_hash_of

COMPANIES = [
    "Nordvik Tech", "Fjällström Data", "Kustlinje Systems", "Björkdal Software",
    "Solgläntan AI", "Havsörn Cloud", "Granskog Fintech", "Älvdal Robotics",
]

# city -> weight (more tech jobs in Stockholm)
CITIES = {"Stockholm": 6, "Göteborg": 3, "Malmö": 2, "Uppsala": 1, "Linköping": 1, "remote": 1}

SENIORITY = {"junior": 6, "intern": 2, "mid": 2, "unspecified": 1}

SWEDISH = {"required": 3, "preferred": 3, "not_mentioned": 4}

# Role templates: title, the skills this kind of role always requires, a pool
# to draw extra required skills from, and a pool of nice-to-haves.
ROLES = [
    {"title": "Backend Developer", "core": ["Java", "Spring"],
     "extra": ["PostgreSQL", "Docker", "REST APIs", "Git", "Kafka"],
     "nice": ["AWS", "Kubernetes", "Kotlin", "Terraform"]},
    {"title": "Backendutvecklare", "core": ["Python", "Django"],
     "extra": ["PostgreSQL", "Docker", "REST APIs", "Git", "Redis"],
     "nice": ["AWS", "Celery", "Kubernetes", "CI/CD"]},
    {"title": "Frontend Developer", "core": ["TypeScript", "React"],
     "extra": ["CSS", "HTML", "Git", "Next.js", "Jest"],
     "nice": ["GraphQL", "Figma", "Node.js", "Accessibility"]},
    {"title": "Fullstack Developer", "core": ["C#", ".NET"],
     "extra": ["SQL Server", "Azure", "TypeScript", "React", "Git"],
     "nice": ["Docker", "Kubernetes", "Blazor", "CI/CD"]},
    {"title": "Data Engineer", "core": ["Python", "SQL"],
     "extra": ["Airflow", "Spark", "dbt", "PostgreSQL", "Git"],
     "nice": ["AWS", "Kafka", "Snowflake", "Terraform"]},
    {"title": "DevOps Engineer", "core": ["Linux", "Docker"],
     "extra": ["Kubernetes", "Terraform", "CI/CD", "Bash", "Git"],
     "nice": ["AWS", "Azure", "Prometheus", "Grafana", "Python"]},
    {"title": "Embedded Software Developer", "core": ["C", "C++"],
     "extra": ["Linux", "Git", "RTOS", "Python"],
     "nice": ["Rust", "Yocto", "CAN bus", "Jenkins"]},
    {"title": "Machine Learning Engineer", "core": ["Python", "PyTorch"],
     "extra": ["SQL", "Docker", "Git", "scikit-learn"],
     "nice": ["AWS", "Kubernetes", "LLMs", "MLflow"]},
]

SWEDISH_LINE = {
    "required": "Du talar och skriver flytande svenska.",
    "preferred": "Swedish is a plus.",
    "not_mentioned": "",
}


def weighted(choices):
    """Pick one key from a {value: weight} dict."""
    return random.choices(list(choices), weights=list(choices.values()))[0]


def build_ad():
    """One fake ad as a dict of the same fields the LLM would return."""
    role = random.choice(ROLES)
    seniority = weighted(SENIORITY)
    required = role["core"] + random.sample(role["extra"], k=random.randint(1, 3))
    nice = random.sample(role["nice"], k=random.randint(0, 2))

    prefix = {"junior": "Junior ", "intern": "Intern: "}.get(seniority, "")
    return {
        "title": prefix + role["title"],
        "company": random.choice(COMPANIES),
        "city": weighted(CITIES),
        "seniority": seniority,
        "required_skills": required,
        "nice_to_have_skills": nice,
        "swedish_requirement": weighted(SWEDISH),
    }


def raw_text_for(data):
    """Write a short, readable ad from the fields."""
    lines = [
        f"{data['title']} — {data['company']} | {data['city']}",
        "",
        f"You have experience with {', '.join(data['required_skills'])}.",
    ]
    if data["nice_to_have_skills"]:
        lines.append(f"Meriterande: {', '.join(data['nice_to_have_skills'])}.")
    if SWEDISH_LINE[data["swedish_requirement"]]:
        lines.append(SWEDISH_LINE[data["swedish_requirement"]])
    return "\n".join(lines)


class Command(BaseCommand):
    help = "Load fictional mock job ads (no LLM calls)."

    def add_arguments(self, parser):
        parser.add_argument("--flush", action="store_true", help="delete all ads data first")
        parser.add_argument("--count", type=int, default=40, help="how many ads (default 40)")

    # atomic: if anything fails halfway, nothing is saved — no half-seeded DB.
    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(42)

        if options["flush"]:
            # Children before parents: AdSkill points at JobAd and Skill,
            # JobAd points at Company (PROTECT would block the other order).
            AdSkill.objects.all().delete()
            JobAd.objects.all().delete()
            Skill.objects.all().delete()
            Company.objects.all().delete()
            self.stdout.write("flushed ads tables")

        now = timezone.now()
        created = skipped = 0
        for _ in range(options["count"]):
            data = build_ad()
            raw_text = raw_text_for(data)
            content_hash = content_hash_of(raw_text)
            # Day 8: same text twice is a duplicate now — the database would
            # refuse it. (Re-running seed without --flush skips everything,
            # because random.seed(42) makes the same ads again.)
            if JobAd.objects.filter(content_hash=content_hash).exists():
                skipped += 1
                continue
            created += 1
            company, _ = Company.objects.get_or_create(name=data["company"])
            ad = JobAd.objects.create(
                raw_text=raw_text,
                content_hash=content_hash,
                status="completed",
                company=company,
                title=data["title"],
                city=data["city"],
                seniority=data["seniority"],
                swedish_requirement=data["swedish_requirement"],
            )
            for level, key in [("required", "required_skills"), ("nice_to_have", "nice_to_have_skills")]:
                for name in data[key]:
                    skill, _ = Skill.objects.get_or_create(name=name)
                    AdSkill.objects.get_or_create(ad=ad, skill=skill, defaults={"level": level})

            # created_at is auto_now_add (always "now" on create), so spread the
            # ads over the last 60 days with an update afterwards.
            JobAd.objects.filter(id=ad.id).update(
                created_at=now - timedelta(days=random.randint(0, 60), hours=random.randint(0, 23))
            )

        self.stdout.write(self.style.SUCCESS(
            f"seeded {created} ads ({skipped} skipped as duplicates). In the database: "
            f"{JobAd.objects.count()} ads, {Company.objects.count()} companies, "
            f"{Skill.objects.count()} skills, {AdSkill.objects.count()} ad-skill links"
        ))
