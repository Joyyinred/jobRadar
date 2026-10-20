"""
Your job applications: which ads you're interested in, where each one stands,
and how it got there.

A separate app from `ads` because the two hold different kinds of data:
  ads       — shared: every user sees the same ads, and the market statistics
              are over all of them
  tracking  — personal: each row belongs to one user, and only they see it
tracking depends on ads (an application is FOR an ad); ads never knows that
tracking exists. One direction only: ads can be understood, tested and changed
without thinking about applications.

Two tables, because "where is it now" and "what happened when" are different
questions:
  Application       — the current state (one row per user per ad)
  ApplicationEvent  — every status change, with its time (append-only)
With only the current status you know "rejected" but not "rejected 12 days
after applying" — the history is what answers "how long do companies take?"
and "how many of my applications got an interview?".
"""
from django.conf import settings
from django.db import models

from ads.models import JobAd


class Application(models.Model):
    STATUS_CHOICES = [
        ("saved", "saved"),           # interested, not applied yet
        ("applied", "applied"),
        ("interview", "interview"),
        ("offer", "offer"),
        ("rejected", "rejected"),     # their no
        ("withdrawn", "withdrawn"),   # your no
    ]

    # CASCADE: delete a user, their applications go too — they're only theirs.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="applications")
    # CASCADE too, though ads are never deleted (removed ads are kept, see JobAd.removed_at).
    ad = models.ForeignKey(JobAd, on_delete=models.CASCADE, related_name="applications")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="saved")
    notes = models.TextField(blank=True, default="")  # "spoke to Anna", "asked for portfolio"
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # One application per user per ad. Enforced by the database: two
            # quick clicks on "Save" can't make two rows.
            models.UniqueConstraint(fields=["user", "ad"], name="one_application_per_user_and_ad"),
        ]

    def __str__(self):
        return f"{self.user} -> ad {self.ad_id}: {self.status}"


class ApplicationEvent(models.Model):
    """One status change: this application moved to `status` at `at`.

    Only ever added to, never edited — a log of what happened. The first event
    of an application is its first status (usually "saved").
    """

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(max_length=20, choices=Application.STATUS_CHOICES)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at", "id"]
