"""
Market statistics: what do the collected ads ask for?

Answers the requirements doc's questions, e.g. "among junior roles, what share
ask for Java vs Python?" and "what share list Swedish as a hard requirement?"

All counting is done by the database (SQL COUNT), never by the LLM: exact,
repeatable, and free. The LLM's only part was turning each ad into rows.

Honesty rules (REQUIREMENTS_DOC):
  - fewer than MIN_SAMPLE ads under a filter -> refuse, don't report a
    percentage from 4 data points
  - the numbers describe the ads JobRadar collected, not the whole market,
    and the response says so
"""
from django.db.models import Count, Q
from django.db.models.functions import Lower

from .exceptions import InsufficientDataError
from .models import AdSkill, JobAd

MIN_SAMPLE = 30  # a starting point, not a statistical claim (DESIGN_DOC §15)

# Not ranked as skills: languages have their own field (swedish_requirement),
# and counting them here too would count the same requirement twice.
LANGUAGE_NAMES = {"swedish", "english", "svenska", "engelska"}

SAMPLE_NOTE = (
    "Counts ads collected by JobRadar (pasted, plus Platsbanken imports — one batch "
    "of the newest Data/IT ads and one search for 'junior'), not the whole market. "
    "The same role posted twice counts twice."
)


def skill_frequency(seniority=None, level=None, top=20):
    """How many of the matching ads mention each skill.

    seniority: list like ["junior", "intern"], or None for all
    level:     "required" / "nice_to_have", or None for either
    Returns a dict ready for the API (see MarketSkillsView).
    Raises InsufficientDataError if fewer than MIN_SAMPLE ads match.
    """
    ads = JobAd.objects.filter(status="completed")
    if seniority:
        ads = ads.filter(seniority__in=seniority)

    sample_size = ads.count()
    if sample_size < MIN_SAMPLE:
        raise InsufficientDataError(
            "SAMPLE_TOO_SMALL",
            f"Only {sample_size} ads match — at least {MIN_SAMPLE} are needed for a percentage to mean anything.",
            {"sample_size": sample_size, "min_sample": MIN_SAMPLE},
        )

    links = AdSkill.objects.filter(ad__in=ads)
    if level:
        links = links.filter(level=level)

    # One SQL query: group the skill links by lower-cased name ("Cloud" and
    # "cloud" are one skill), count DISTINCT ads per group (an ad listing a
    # skill twice counts once), split by level.
    links = links.annotate(key=Lower("skill__name")).exclude(key__in=LANGUAGE_NAMES)
    rows = list(
        links.values("key")
        .annotate(
            ads=Count("ad", distinct=True),
            required=Count("ad", filter=Q(level="required"), distinct=True),
            nice_to_have=Count("ad", filter=Q(level="nice_to_have"), distinct=True),
        )
        .order_by("-ads", "key")[:top]
    )
    names = display_names(links, [row["key"] for row in rows])

    return {
        "sample_size": sample_size,
        "filters": {"seniority": seniority or "all", "level": level or "any"},
        "skills": [
            {
                "name": names[row["key"]],
                "ads": row["ads"],
                "share": round(row["ads"] / sample_size, 3),
                "required": row["required"],
                "nice_to_have": row["nice_to_have"],
            }
            for row in rows
        ],
        "swedish_requirement": swedish_requirement_shares(ads, sample_size),
        "note": SAMPLE_NOTE,
    }


def display_names(links, keys):
    """For each lower-cased key, the spelling used most often ("Python" over
    "python" if more ads wrote it that way); on a tie, a capitalised one.

    Not Min(name) in SQL: which of "Cloud" / "cloud" sorts first depends on
    the database's collation, and on Postgres it's the lower-case one.
    """
    best = {}
    spellings = links.filter(key__in=keys).values("key", "skill__name").annotate(n=Count("id"))
    for row in spellings:
        name = row["skill__name"]
        rank = (row["n"], name != name.lower(), name)  # most used, then capitalised, then stable
        if row["key"] not in best or rank > best[row["key"]][0]:
            best[row["key"]] = (rank, name)
    return {key: name for key, (_, name) in best.items()}


def swedish_requirement_shares(ads, sample_size):
    """{"required": 0.42, "preferred": 0.1, "not_mentioned": 0.48} over the same ads."""
    counts = dict(ads.values_list("swedish_requirement").annotate(n=Count("id")))
    return {
        value: round(counts.get(value, 0) / sample_size, 3)
        for value in ("required", "preferred", "not_mentioned")
    }
