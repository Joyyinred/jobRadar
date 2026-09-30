"""
Day 2: ONE table on purpose.

Everything about an ad lives in one row, and the parsed fields are one JSON blob.
Day 3 asks "is one table enough?" — with this design you'll feel why it isn't
(e.g. "Klarna" and "Klarna AB" are just strings inside JSON; counting skills
means reading every blob).
"""
from django.db import models


class JobAd(models.Model):
    # The lifecycle of one parse. Day 2 is synchronous, so the user will almost
    # never *see* pending/processing — keep that in mind for Day 4.
    STATUS_CHOICES = [
        ("pending", "pending"),
        ("processing", "processing"),
        ("completed", "completed"),
        ("failed", "failed"),
    ]

    # The ad exactly as pasted. Never modified — it's the source of truth,
    # so every ad can be re-parsed when the prompt improves.
    raw_text = models.TextField()

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")

    # The 5 fields the LLM extracts, stored as one JSON object:
    # {"title", "company", "seniority", "required_skills", "swedish_requirement"}
    # null until status == "completed".
    result = models.JSONField(null=True, blank=True)

    # A short, safe message when status == "failed". Never a stack trace.
    error = models.CharField(max_length=200, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"JobAd {self.id} ({self.status})"
