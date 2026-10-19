"""
The HTTP front desk. Each view only:
  1. reads the request (through a serializer),
  2. asks services.py to do the work,
  3. picks the status code and returns the response (through a serializer).

No LLM, no queue, no database queries in here — see services.py and llm.py.
No error responses either: problems are raised (in serializers or services)
and turned into one JSON format by exceptions.exception_handler.
"""
from rest_framework import status as http_status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import market, services
from .adapters import PlatsbankenUrlAdapter
from .pagination import AdPagination
from .serializers import (
    AdDetailSerializer,
    AdListQuerySerializer,
    AdSummarySerializer,
    CreateAdSerializer,
    ImportJobSerializer,
    JobTechSearchImportSerializer,
    MarketSkillsQuerySerializer,
    PlatsbankenImportSerializer,
)


@api_view(["GET", "POST"])
def ads_collection(request):
    """/api/ads/ — one URL, two actions. The HTTP method decides which."""
    if request.method == "GET":
        return list_ads(request)
    return create_ad(request)


def list_ads(request):
    """GET /api/ads/?seniority=junior&seniority=intern&employment_type=thesis&occupation_group=<code>&origin=user&page=2

    A short summary of each ad, newest first, 20 per page. All filters optional.
    """
    data = query_params(request, AdListQuerySerializer, lists=["seniority"])
    ads = services.list_ads(
        seniority=data.get("seniority"),
        employment_type=data.get("employment_type"),
        occupation_group=data.get("occupation_group"),
        origin=data.get("origin"),
    )
    # The paginator adds LIMIT/OFFSET to the query (plus one COUNT), so only
    # this page's 20 rows are read from the database — not all, then sliced.
    paginator = AdPagination()
    page = paginator.paginate_queryset(ads, request)
    return paginator.get_paginated_response(AdSummarySerializer(page, many=True).data)


def query_params(request, serializer_class, lists=()):
    """Validate a query string with a serializer.

    A key can repeat (?seniority=junior&seniority=intern); for the keys in
    `lists`, getlist() keeps every value — plain .get() would keep only the last.
    """
    params = request.query_params.dict()
    for key in lists:
        values = request.query_params.getlist(key)
        if values:
            params[key] = values
        else:
            params.pop(key, None)
    serializer = serializer_class(data=params)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


@api_view(["GET"])
def list_occupation_groups(request):
    """GET /api/occupation-groups/ — JobTech groups in the data, for filter drop-downs."""
    return Response(services.occupation_groups())


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
    """POST /api/imports/jobtech-search/   body: {"query": "...", "limit": 20, "data_it_only": false}

    Doesn't wait for the import (it can take a minute): records it, queues it
    for a worker, and answers 202 with the job. Poll GET /api/imports/<id>/.
    """
    serializer = JobTechSearchImportSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    job = services.start_jobtech_import(serializer.validated_data)
    return Response(ImportJobSerializer(job).data, status=http_status.HTTP_202_ACCEPTED)


@api_view(["GET"])
def get_import(request, job_id):
    """GET /api/imports/<job_id>/ — a background import's status and progress."""
    job = services.get_import(job_id)  # missing -> 404, via the handler
    return Response(ImportJobSerializer(job).data)


# --- Market statistics -----------------------------------------------------------

@api_view(["GET"])
def market_skills(request):
    """GET /api/market/skills/?seniority=junior&seniority=intern&level=required&top=20

    Fewer than market.MIN_SAMPLE matching ads -> 422 (via the handler).
    """
    data = query_params(request, MarketSkillsQuerySerializer, lists=["seniority"])
    return Response(market.skill_frequency(
        seniority=data.get("seniority"), level=data.get("level"), top=data["top"],
        occupation_group=data.get("occupation_group"),
    ))
