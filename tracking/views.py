"""
Your applications. Everything here needs a login, reading included: it's
personal data, unlike the ads and market stats anyone may browse.

    GET    /api/applications/?status=applied   your list + summary numbers
    GET    /api/ads/<id>/application/          {"application": {...} | null}
    PUT    /api/ads/<id>/application/          {"status"?, "notes"?} -> start / update
    DELETE /api/ads/<id>/application/          stop tracking

PUT, not POST: the URL names exactly one thing — YOUR application for THIS ad —
and sending the same body twice leaves it the same (idempotent). There's no
separate "create": the first PUT creates it (201), later ones update (200).
"""
from rest_framework import status as http_status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from ads.views import query_params

from . import services
from .serializers import ApplicationListQuerySerializer, ApplicationSerializer, TrackSerializer


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_applications(request):
    data = query_params(request, ApplicationListQuerySerializer)
    apps = services.list_applications(request.user, status=data.get("status"))
    return Response({
        "results": ApplicationSerializer(apps, many=True).data,
        "summary": services.summary(request.user),
    })


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def ad_application(request, ad_id):
    if request.method == "GET":
        app = services.get_application(request.user, ad_id)
        # Not tracking an ad is normal, not an error: 200 with null, not 404.
        # (A 404 here means the AD doesn't exist.)
        return Response({"application": ApplicationSerializer(app).data if app else None})

    if request.method == "DELETE":
        services.untrack(request.user, ad_id)
        return Response(status=http_status.HTTP_204_NO_CONTENT)

    body = TrackSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    app, created = services.track(
        request.user, ad_id, status=body.validated_data.get("status"), notes=body.validated_data.get("notes"),
    )
    app = services.get_application(request.user, ad_id)  # fresh, with its events
    return Response(
        {"application": ApplicationSerializer(app).data},
        status=http_status.HTTP_201_CREATED if created else http_status.HTTP_200_OK,
    )
