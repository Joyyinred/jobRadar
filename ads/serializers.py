"""
The shape of data crossing the API boundary, in both directions:

  in:   request JSON  -> clean Python values   (CreateAdSerializer)
  out:  JobAd objects -> response JSON         (the other three)

Day 7: same output as before, just moved here from views.py. Validation rules
(empty ad, max length...) come on Day 8 — this is where they'll go.
"""
from rest_framework import serializers


# --- In ----------------------------------------------------------------------

class CreateAdSerializer(serializers.Serializer):
    """Body of POST /api/ads/: {"raw_text": "..."}"""

    # Same behaviour as the old request.data.get("raw_text", ""): missing -> "",
    # empty allowed. No rules yet (Day 8).
    # trim_whitespace=False: DRF strips spaces by default; we store the raw ad
    # exactly as pasted.
    raw_text = serializers.CharField(default="", allow_blank=True, trim_whitespace=False)


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
