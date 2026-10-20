"""
The rules of tracking applications. Views call these; every function takes
the user, and only ever touches that user's rows — "your applications" is
enforced here, once, not in each view.
"""
from statistics import median

from django.db import transaction

from ads import services as ads_services

from .models import Application, ApplicationEvent

# Statuses that mean the company answered.
HEARD_BACK = {"interview", "offer", "rejected"}
# Statuses that mean you had applied: an interview or a rejection implies it,
# even if you never clicked "applied". Not "withdrawn": you can drop an ad
# that you only saved.
APPLIED_OR_LATER = {"applied"} | HEARD_BACK


def list_applications(user, status=None):
    """Your applications, most recently changed first."""
    apps = (
        Application.objects.filter(user=user)
        .select_related("ad", "ad__company")  # one query, not one per row
        .prefetch_related("events")           # and one more for all their events
        .order_by("-updated_at", "-id")
    )
    if status:
        apps = apps.filter(status=status)
    return apps


def get_application(user, ad_id):
    """Your application for this ad, or None if you're not tracking it."""
    ads_services.get_ad(ad_id)  # an ad that doesn't exist is a 404, not "not tracked"
    return Application.objects.filter(user=user, ad_id=ad_id).prefetch_related("events").first()


def track(user, ad_id, status=None, notes=None):
    """Start or update tracking an ad. Returns (application, created).

    status: the new status, or None to leave it ("saved" for a new one)
    notes:  the new notes, or None to leave them

    Any status can follow any other: real applications don't follow a script
    (an offer straight after applying; "rejected" set by mistake and undone).
    Every actual change is logged as an event; setting the same status again
    logs nothing.
    """
    ad = ads_services.get_ad(ad_id)
    # atomic: the application and its event are saved together, or neither.
    with transaction.atomic():
        # get_or_create relies on the unique (user, ad) constraint: two "Save"
        # clicks at the same moment still give one row.
        app, created = Application.objects.get_or_create(
            user=user, ad=ad, defaults={"status": status or "saved", "notes": notes or ""},
        )
        if created:
            ApplicationEvent.objects.create(application=app, status=app.status)
            return app, True

        changed = []
        if status and status != app.status:
            app.status = status
            changed.append("status")
            ApplicationEvent.objects.create(application=app, status=status)
        if notes is not None and notes != app.notes:
            app.notes = notes
            changed.append("notes")
        if changed:
            app.save(update_fields=[*changed, "updated_at"])
    return app, False


def untrack(user, ad_id):
    """Stop tracking: the application and its history are deleted.
    Fine to call when not tracking (nothing to delete) — the end state is the same."""
    Application.objects.filter(user=user, ad_id=ad_id).delete()


def applied_at(app):
    """When you first applied: the earliest event that means you had."""
    times = [e.at for e in app.events.all() if e.status in APPLIED_OR_LATER]
    return min(times) if times else None


def summary(user):
    """Counts per status, and how companies respond to your applications.

    applied:        applications that got past "saved" (you applied, at least)
    heard_back:     of those, how many got an answer (interview, offer, rejection)
    days_to_answer: median days from applying to the first answer — median,
                    not mean: one company taking three months shouldn't make
                    the typical wait look like a month
    """
    apps = list(Application.objects.filter(user=user).prefetch_related("events"))
    counts = {code: 0 for code, _ in Application.STATUS_CHOICES}
    waits = []
    applied = heard_back = 0
    for app in apps:
        counts[app.status] += 1
        start = applied_at(app)
        if start is None:
            continue
        applied += 1
        answers = [e.at for e in app.events.all() if e.status in HEARD_BACK and e.at >= start]
        if answers:
            heard_back += 1
            waits.append((min(answers) - start).total_seconds() / 86400)
    return {
        "counts": counts,
        "applied": applied,
        "heard_back": heard_back,
        "days_to_answer": round(median(waits), 1) if waits else None,
    }
