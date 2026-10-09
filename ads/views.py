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
from .adapters import JobTechSearchAdapter, PlatsbankenUrlAdapter
from .serializers import (
    AdDetailSerializer,
    AdSummarySerializer,
    CreateAdSerializer,
    JobTechSearchImportSerializer,
    PlatsbankenImportSerializer,
)


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

    return ingest_response(ad, created)


def ingest_response(ad, created):
    """The reply for "one ad in": the same for every source.

    202 Accepted = new ad, "got it, not done yet" — the LLM call happens in the worker.
    200 OK + duplicate = the ad we already have; 202 would mean "new work
    started", and none did.
    """
    body = AdDetailSerializer(ad).data
    if not created:
        body["duplicate"] = True
        return Response(body, status=http_status.HTTP_200_OK)
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


# --- Imports (Day 9): other sources, same ingest() underneath -----------------

@api_view(["POST"])
def import_platsbanken(request):
    """POST /api/imports/platsbanken/   body: {"url": "https://arbetsformedlingen.se/platsbanken/annonser/31575359", "confirm": false}"""
    serializer = PlatsbankenImportSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    data = serializer.validated_data

    # Not a Platsbanken link -> 400; removed ad -> 404; JobTech down -> 502;
    # a warning -> 409 (send again with confirm) — all raised, all via the handler.
    adapter = PlatsbankenUrlAdapter(data["url"])
    ad, created = services.ingest_one(adapter, confirm=data["confirm"])
    return ingest_response(ad, created)


@api_view(["POST"])
def import_jobtech_search(request):
    """POST /api/imports/jobtech-search/   body: {"query": "junior utvecklare", "limit": 20}

    Answers with counts; the new ads are queued and show up in the list.
    """
    serializer = JobTechSearchImportSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    counts = services.ingest_batch(JobTechSearchAdapter(data["query"], data["limit"]))
    return Response(counts, status=http_status.HTTP_200_OK)
