"""
The HTTP front desk. Each view only:
  1. reads the request (through a serializer),
  2. asks services.py to do the work,
  3. picks the status code and returns the response (through a serializer).

No LLM, no queue, no database queries in here — see services.py and llm.py.

Still deliberately missing (Day 8): input validation, duplicate detection,
one error format for everything, tests.
"""
from django.shortcuts import render
from rest_framework import status as http_status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import services
from .models import JobAd  # only for its DoesNotExist exception
from .serializers import AdDetailSerializer, AdSummarySerializer, CreateAdSerializer


def index(request):
    """Serve the one HTML page. The page talks to the API with fetch()."""
    return render(request, "ads/index.html")


@api_view(["GET", "POST"])
def ads_collection(request):
    """/api/ads/ — one URL, two actions. The HTTP method decides which."""
    if request.method == "GET":
        return list_ads(request)
    return create_ad(request)


def list_ads(request):
    """GET /api/ads/ — a short summary of every ad, newest first."""
    ads = services.list_ads()
    return Response(AdSummarySerializer(ads, many=True).data)


def create_ad(request):
    """POST /api/ads/   body: {"raw_text": "..."}"""
    serializer = CreateAdSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    ad = services.submit_ad(serializer.validated_data["raw_text"])
    # 202 Accepted = "got it, not done yet". The LLM call happens in the worker.
    return Response(AdDetailSerializer(ad).data, status=http_status.HTTP_202_ACCEPTED)


@api_view(["GET"])  # only GET is allowed here; a POST to this URL gets 405
def get_ad(request, ad_id):
    """GET /api/ads/<ad_id>/"""
    try:
        ad = services.get_ad(ad_id)
    except JobAd.DoesNotExist:
        return Response({"error": f"Ad {ad_id} not found"}, status=http_status.HTTP_404_NOT_FOUND)
    return Response(AdDetailSerializer(ad).data)  # 200 OK is the default


@api_view(["POST"])  # POST, not GET: it changes state (failed -> pending) and queues work
def retry_ad(request, ad_id):
    """POST /api/ads/<ad_id>/retry/ — re-queue an ad whose parse failed."""
    ad = services.retry_ad(ad_id)
    if ad is not None:
        return Response(AdDetailSerializer(ad).data, status=http_status.HTTP_202_ACCEPTED)

    # Not re-queued: either the ad doesn't exist (404), or it isn't "failed" (409).
    try:
        current = services.get_ad(ad_id)
    except JobAd.DoesNotExist:
        return Response({"error": f"Ad {ad_id} not found"}, status=http_status.HTTP_404_NOT_FOUND)
    # 409 Conflict: the request clashes with the ad's current state.
    return Response(
        {"error": f"Ad {ad_id} is {current.status}; only failed ads can be retried"},
        status=http_status.HTTP_409_CONFLICT,
    )
