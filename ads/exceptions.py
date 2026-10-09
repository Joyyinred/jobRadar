"""
Day 8 Part 2: one error format for the whole API.

Every non-success response looks like this, whatever went wrong:

    {
      "type":    "validation" | "not_found" | "block" | "warning" | "error",
      "code":    "AD_TOO_SHORT",          <- stable, for code to check
      "message": "Human-readable text",   <- for the user; may change wording
      "detail":  {...}                    <- extra facts (fields, ids, limits)
    }

How it's used:
  - services.py / views.py only `raise SomeError(...)`; they never build an
    error Response themselves.
  - exception_handler() below is the ONE place that turns any exception into
    that JSON. Want to change the format? Change it here, nowhere else.

Wired up in settings.py: REST_FRAMEWORK["EXCEPTION_HANDLER"].
"""
import logging

from rest_framework import exceptions as drf_exceptions
from rest_framework import status as http_status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.views import set_rollback

logger = logging.getLogger(__name__)


class BaseAppException(Exception):
    """What every app error has. Subclasses only fill in type and status."""

    type = "error"
    http_status = http_status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, code, message, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    def to_dict(self):
        return {"type": self.type, "code": self.code, "message": self.message, "detail": self.detail}


class ValidationError(BaseAppException):
    """The input itself is wrong (empty ad, bad URL). Fix it and send again."""

    type = "validation"
    http_status = http_status.HTTP_400_BAD_REQUEST


class NotFoundError(BaseAppException):
    """The thing asked for doesn't exist."""

    type = "not_found"
    http_status = http_status.HTTP_404_NOT_FOUND


class BlockError(BaseAppException):
    """A business rule says no (e.g. retrying an ad that didn't fail). Can't continue."""

    type = "block"
    http_status = http_status.HTTP_409_CONFLICT


class UpstreamError(BaseAppException):
    """A service we depend on (e.g. the JobTech API) failed or timed out.
    Not the user's fault and not ours: 502 Bad Gateway. Trying later may work."""

    type = "upstream"
    http_status = http_status.HTTP_502_BAD_GATEWAY


class WarningException(BaseAppException):
    """Allowed, but only if the user confirms. Nothing was saved.

    409, not 200: the request did NOT do what was asked (no ad was created),
    so it isn't a success. The frontend's one error branch catches it and
    shows a confirm button because type == "warning".
    """

    type = "warning"
    http_status = http_status.HTTP_409_CONFLICT


def _flatten(messages):
    """DRF error details come as a list, a single string, or a nested dict
    (nested serializers). Turn any of them into one readable string."""
    if isinstance(messages, dict):
        return " ".join(f"{key}: {_flatten(value)}" for key, value in messages.items())
    if isinstance(messages, (list, tuple)):
        return " ".join(_flatten(m) for m in messages)
    return str(messages)


def exception_handler(exc, context):
    """DRF calls this for every exception raised in a view. One format out."""
    # 1. Our own errors: already know their shape.
    if isinstance(exc, BaseAppException):
        set_rollback()  # same as DRF does: undo a request-wide transaction, if any
        return Response(exc.to_dict(), status=exc.http_status)

    # 2. Let DRF's own handler go first. It knows things we'd otherwise have to
    #    copy: Django's Http404 -> 404 and PermissionDenied -> 403, response
    #    headers like Retry-After (throttling), and transaction rollback.
    #    It returns None for anything it doesn't recognise.
    response = drf_exception_handler(exc, context)

    # 3. Not recognised -> a bug. Full traceback to the log for us; a safe,
    #    generic message to the user — never a stack trace in the response.
    if response is None:
        view = context.get("view")
        logger.exception("unhandled error in %s", view.__class__.__name__ if view else "?")
        body = {"type": "error", "code": "INTERNAL_ERROR", "message": "Something went wrong. Please try again.", "detail": {}}
        return Response(body, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

    # 4. Recognised: keep DRF's status code and headers, replace the body with
    #    our shape.
    if isinstance(exc, drf_exceptions.ValidationError):
        # Serializer errors: {"raw_text": ["msg"], ...} — field messages go in detail.
        fields = response.data if isinstance(response.data, dict) else {"non_field_errors": response.data}
        error = ValidationError("INVALID_INPUT", _flatten(fields), {"fields": fields})
        response.data = error.to_dict()
        return response

    # Everything else DRF knows (404, 403, 405, malformed JSON, ...) arrives as
    # {"detail": ErrorDetail("text", code="not_found")}.
    detail = response.data.get("detail", "") if isinstance(response.data, dict) else response.data
    code = getattr(detail, "code", None) or "error"
    response.data = {
        "type": "not_found" if response.status_code == http_status.HTTP_404_NOT_FOUND else "error",
        "code": str(code).upper(),
        "message": _flatten(detail),
        "detail": {},
    }
    return response
