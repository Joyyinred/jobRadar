"""
The HTTP front desk. Each view only:
  1. reads the request (through a serializer),
  2. asks services.py to do the work,
  3. picks the status code and returns the response (through a serializer).

No LLM, no queue, no database queries in here — see services.py and llm.py.
No error responses either: problems are raised (in serializers or services)
and turned into one JSON format by exceptions.exception_handler.
"""
from django.shortcuts import render
from rest_framework import status as http_status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import services
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
    """POST /api/ads/   body: {"raw_text": "...", "source_url": "...", "confirm": false}"""
    serializer = CreateAdSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)  # invalid -> 400, via the handler
    data = serializer.validated_data

    # May raise WarningException (409, via the handler) — nothing to do here.
    ad, created = services.submit_ad(data["raw_text"], data.get("source_url"), data["confirm"])

    body = AdDetailSerializer(ad).data
    if not created:
        # Exact duplicate: the ad we already have. 200 = "here it is";
        # 202 would mean "new work started", and none did.
        body["duplicate"] = True
        return Response(body, status=http_status.HTTP_200_OK)
    # 202 Accepted = "got it, not done yet". The LLM call happens in the worker.
    return Response(body, status=http_status.HTTP_202_ACCEPTED)


@api_view(["GET"])  # only GET is allowed here; a POST to this URL gets 405
def get_ad(request, ad_id):
    """GET /api/ads/<ad_id>/"""
    ad = services.get_ad(ad_id)  # missing -> NotFoundError -> 404, via the handler
    return Response(AdDetailSerializer(ad).data)  # 200 OK is the default


@api_view(["POST"])  # POST, not GET: it changes state (failed -> pending) and queues work
def retry_ad(request, ad_id):
    """POST /api/ads/<ad_id>/retry/ — re-queue an ad whose parse failed."""
    ad = services.retry_ad(ad_id)  # missing -> 404, not failed -> 409, via the handler
    return Response(AdDetailSerializer(ad).data, status=http_status.HTTP_202_ACCEPTED)
