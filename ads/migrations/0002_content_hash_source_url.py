"""
Day 8: add content_hash (unique) and source_url (unique, optional).

Existing rows have no hash yet, so this runs in three steps:
  1. add content_hash allowing NULL
  2. compute the hash for every existing ad
  3. make it NOT NULL + UNIQUE
(Duplicate texts were deleted by hand before this ran, keeping the oldest.)
"""
import hashlib
import re

from django.db import migrations, models


def backfill_hashes(apps, schema_editor):
    # A copy of services.content_hash_of, frozen here on purpose: a migration
    # must keep giving the same result even if the app code changes later.
    JobAd = apps.get_model("ads", "JobAd")
    for ad in JobAd.objects.all():
        normalised = re.sub(r"\s+", " ", ad.raw_text.replace("\r\n", "\n")).strip()
        ad.content_hash = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
        ad.save(update_fields=["content_hash"])


class Migration(migrations.Migration):

    dependencies = [
        ("ads", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobad",
            name="content_hash",
            field=models.CharField(max_length=64, null=True),
        ),
        migrations.RunPython(backfill_hashes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="jobad",
            name="content_hash",
            field=models.CharField(max_length=64, unique=True),
        ),
        migrations.AddField(
            model_name="jobad",
            name="source_url",
            field=models.URLField(blank=True, max_length=500, null=True, unique=True),
        ),
    ]
