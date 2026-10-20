from rest_framework import serializers

from .models import Application
from .services import applied_at

STATUSES = [code for code, _ in Application.STATUS_CHOICES]


# --- In -----------------------------------------------------------------------

class TrackSerializer(serializers.Serializer):
    """Body of PUT /api/ads/<id>/application/ — both optional: {} just saves the ad."""

    status = serializers.ChoiceField(choices=STATUSES, required=False)
    notes = serializers.CharField(max_length=5000, allow_blank=True, required=False, trim_whitespace=False)


class ApplicationListQuerySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=STATUSES, required=False)


# --- Out ----------------------------------------------------------------------

class TrackedAdSerializer(serializers.Serializer):
    """Enough of the ad to show it in your list without opening it."""

    id = serializers.IntegerField()
    title = serializers.SerializerMethodField()
    company = serializers.SerializerMethodField()
    apply_by = serializers.DateTimeField(source="expires_at", allow_null=True)
    closed = serializers.BooleanField(source="is_closed")

    def get_title(self, ad):
        return ad.title_en or ad.title or None

    def get_company(self, ad):
        return ad.company.name if ad.company else None


class EventSerializer(serializers.Serializer):
    status = serializers.CharField()
    at = serializers.DateTimeField()


class ApplicationSerializer(serializers.Serializer):
    ad = TrackedAdSerializer()
    status = serializers.CharField()
    notes = serializers.CharField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    applied_at = serializers.SerializerMethodField()
    events = EventSerializer(many=True)  # the history, oldest first

    def get_applied_at(self, app):
        at = applied_at(app)
        return serializers.DateTimeField().to_representation(at) if at else None
