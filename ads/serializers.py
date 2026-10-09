"""
The shape of data crossing the API boundary, in both directions:

  in:   request JSON  -> clean Python values   (the *Serializer classes under "In")
  out:  JobAd objects -> response JSON         (under "Out")

Only checks that need nothing but the request itself live here. Rules about
the ad (and anything needing the database) are in services.ingest(), which
every source goes through — including imports that never touch a serializer.
"""
from rest_framework import serializers

# Defined once, in services (which applies it to EVERY source); the serializer
# repeats the check only to give the web form a per-field error.
from .services import MAX_AD_CHARS


# --- In ----------------------------------------------------------------------


class CreateAdSerializer(serializers.Serializer):
    """Body of POST /api/ads/: {"raw_text": "...", "source_url": "...", "confirm": false}

    Only checks that can be answered from the request alone live here.
    Anything that needs the database (duplicates) is in services.py.
    """

    # Required, and over-long ads are rejected rather than cut: the part we'd
    # drop could be the requirements section.
    # trim_whitespace=False: DRF strips spaces by default; we store the raw ad
    # exactly as pasted.
    raw_text = serializers.CharField(max_length=MAX_AD_CHARS, trim_whitespace=False)
    # Optional. Must look like a URL if given.
    source_url = serializers.URLField(max_length=500, required=False, allow_null=True, allow_blank=True)
    # The user saw a warning and said "parse it anyway".
    confirm = serializers.BooleanField(default=False)

    def validate_raw_text(self, value):
        # With trim_whitespace=False, DRF's own blank check lets "   " through.
        if not value.strip():
            raise serializers.ValidationError("The ad is empty.")
        return value

    def validate_source_url(self, value):
        return value or None  # "" -> None: "no URL", so it never matches another ad's URL


class PlatsbankenImportSerializer(serializers.Serializer):
    """Body of POST /api/imports/platsbanken/: {"url": "https://arbetsformedlingen.se/platsbanken/annonser/31575359"}

    Only "is there a string". Whether it's a Platsbanken AD link is the
    adapter's call (PlatsbankenUrlAdapter) — it's the one that reads it.
    """

    url = serializers.CharField(max_length=500)
    # Same as for pasting: the user saw a warning and said "import it anyway".
    confirm = serializers.BooleanField(default=False)


class JobTechSearchImportSerializer(serializers.Serializer):
    """Body of POST /api/imports/jobtech-search/: {"query": "junior utvecklare", "limit": 20}"""

    query = serializers.CharField(max_length=200)
    # JobTech returns at most 100 per page; every ad imported is one LLM call.
    limit = serializers.IntegerField(min_value=1, max_value=100, default=20)


# --- Out ---------------------------------------------------------------------

class AdSummarySerializer(serializers.Serializer):
    """One row of GET /api/ads/ (the list)."""

    id = serializers.IntegerField()
    status = serializers.CharField()
    # SerializerMethodField = "the value comes from the get_<name> method below".
    title = serializers.SerializerMethodField()
    company = serializers.SerializerMethodField()

    def get_title(self, ad):
        return ad.title or None  # "" (not parsed yet) -> null, as before

    def get_company(self, ad):
        return ad.company.name if ad.company else None


class AdResultSerializer(serializers.Serializer):
    """The parsed fields of one ad, gathered back from the four tables.

    Same shape the API returned since Day 2, so the frontend never changed.
    Field order here = key order in the JSON = row order in the page's table.
    """

    title = serializers.CharField()
    company = serializers.SerializerMethodField()
    city = serializers.CharField()
    seniority = serializers.CharField()
    required_skills = serializers.SerializerMethodField()
    nice_to_have_skills = serializers.SerializerMethodField()
    swedish_requirement = serializers.CharField()

    def get_company(self, ad):
        return ad.company.name if ad.company else None

    def get_required_skills(self, ad):
        return self._skill_names(ad, "required")

    def get_nice_to_have_skills(self, ad):
        return self._skill_names(ad, "nice_to_have")

    def _skill_names(self, ad, level):
        # All AdSkill rows of this ad, with their Skill joined in, read once
        # and reused for both lists.
        if not hasattr(self, "_ad_skills"):
            self._ad_skills = list(ad.ad_skills.select_related("skill"))
        return [s.skill.name for s in self._ad_skills if s.level == level]


class AdDetailSerializer(serializers.Serializer):
    """GET /api/ads/<id>/, and the 202 replies of POST and retry.

    Always id + status. Plus "result" once completed, or "error" once failed;
    pending / processing have nothing else to show yet.
    """

    def to_representation(self, ad):
        body = {"id": ad.id, "status": ad.status}
        if ad.status == "completed":
            body["result"] = AdResultSerializer(ad).data
        elif ad.status == "failed":
            body["error"] = ad.error
        return body
