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


class WarningException(BaseAppException):
    """Allowed, but only if the user confirms. Nothing was saved.

    409, not 200: the request did NOT do what was asked (no ad was created),
    so it isn't a success. The frontend's one error branch catches it and
    shows a confirm button because type == "warning".
    """

    type = "warning"
    http_status = http_status.HTTP_409_CONFLICT


def exception_handler(exc, context):
    """DRF calls this for every exception raised in a view. One format out."""
    # 1. Our own errors: already know their shape.
    if isinstance(exc, BaseAppException):
        return Response(exc.to_dict(), status=exc.http_status)

    # 2. DRF's own validation errors (from serializer.is_valid). DRF's shape is
    #    {"raw_text": ["msg"], ...} — fold it into ours, field messages in detail.
    if isinstance(exc, drf_exceptions.ValidationError):
        fields = exc.detail if isinstance(exc.detail, dict) else {"non_field_errors": exc.detail}
        message = " ".join(f"{field}: {' '.join(str(m) for m in msgs)}" for field, msgs in fields.items())
        error = ValidationError("INVALID_INPUT", message, {"fields": fields})
        return Response(error.to_dict(), status=error.http_status)

    # 3. Any other DRF error (405 wrong method, 400 malformed JSON, ...):
    #    keep DRF's status code, use our shape.
    if isinstance(exc, drf_exceptions.APIException):
        body = {"type": "error", "code": exc.default_code.upper(), "message": str(exc.detail), "detail": {}}
        return Response(body, status=exc.status_code)

    # 4. Anything else is a bug. Full traceback to the log for us; a safe,
    #    generic message to the user — never a stack trace in the response.
    view = context.get("view")
    logger.exception("unhandled error in %s", view.__class__.__name__ if view else "?")
    body = {"type": "error", "code": "INTERNAL_ERROR", "message": "Something went wrong. Please try again.", "detail": {}}
    return Response(body, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)
