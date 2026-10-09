"""
Day 3: the one-table design split into four tables.

    Company 1 ── N JobAd          one company posts many ads
    JobAd   N ── N Skill          via AdSkill (the "middle table")

Rule of thumb for where a field goes: "can this differ between two ads?"
  yes -> column on JobAd (title, city, seniority...)
  no  -> its own table, stored once (company name, skill name)
"""
from django.db import models


class Company(models.Model):
    # unique=True: the database itself refuses a second "Klarna" row.
    # ("Klarna" vs "Klarna AB" are still two different strings — not solved yet.)
    name = models.CharField(max_length=200, unique=True)

    def __str__(self):
        return self.name


class Skill(models.Model):
    # Stored once. "Java" in 50 ads = 1 row here + 50 rows in AdSkill.
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class JobAd(models.Model):
    STATUS_CHOICES = [
        ("pending", "pending"),
        ("processing", "processing"),
        ("completed", "completed"),
        ("failed", "failed"),
    ]

    # --- What was submitted, and where the parse is ---
    raw_text = models.TextField()  # never modified: the source of truth

    # Day 8: exact-duplicate protection, enforced by the DATABASE (unique=True),
    # not only by code — two identical requests at the same moment can both
    # pass a check in Python, but only one INSERT can win here.
    # SHA-256 of the normalised raw text (see services.content_hash_of).
    content_hash = models.CharField(max_length=64, unique=True)
    # Where the ad was found. Optional. NOT unique: one careers page URL can
    # hold several different ads — a repeat URL with different text is a
    # warning the user confirms (services.submit_ad), not a silent duplicate.
    # Indexed, because submit_ad looks it up on every submit.
    source_url = models.URLField(max_length=500, null=True, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    error = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    # --- Parsed fields: differ per ad, so they are columns here ---
    # All blank/null until the parse completes.
    title = models.CharField(max_length=300, blank=True, default="")
    city = models.CharField(max_length=100, blank=True, default="")  # per ad, not per company
    seniority = models.CharField(max_length=20, blank=True, default="")
    swedish_requirement = models.CharField(max_length=20, blank=True, default="")

    # Foreign key: store the company's id, not its name.
    # null=True: a failed parse, or an ad that doesn't name the company.
    # on_delete=PROTECT: you can't delete a company that still has ads.
    company = models.ForeignKey(
        Company, null=True, blank=True, on_delete=models.PROTECT, related_name="ads"
    )

    def __str__(self):
        return f"JobAd {self.id} ({self.status})"


class AdSkill(models.Model):
    """One row = "this ad asks for this skill, at this level"."""

    LEVEL_CHOICES = [
        ("required", "required"),
        ("nice_to_have", "nice_to_have"),
    ]

    # CASCADE: delete an ad -> its AdSkill rows go too (they mean nothing alone).
    ad = models.ForeignKey(JobAd, on_delete=models.CASCADE, related_name="ad_skills")
    skill = models.ForeignKey(Skill, on_delete=models.PROTECT, related_name="ad_skills")

    # Lives here, not on Skill: Java can be required in one ad and
    # nice-to-have in another. It describes the relationship.
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES)

    class Meta:
        # The same skill can't be listed twice for the same ad.
        constraints = [
            models.UniqueConstraint(fields=["ad", "skill"], name="unique_skill_per_ad"),
        ]

    def __str__(self):
        return f"ad {self.ad_id}: {self.skill} ({self.level})"
