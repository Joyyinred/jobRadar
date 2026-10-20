"""
The authentication class DRF uses for every request (settings.REST_FRAMEWORK).

A module of its own, importing nothing but DRF's authentication: DRF imports
it while loading its settings, and a module that also imports DRF views
(like auth.py) would be imported half-way through — a circular import.
"""
from rest_framework import authentication


class SessionAuthentication(authentication.SessionAuthentication):
    """DRF's session authentication, answering 401 instead of 403 when not logged in.

    401 = "who are you?" (log in and try again); 403 = "I know who you are,
    and no". DRF can only send 401 with a WWW-Authenticate header naming the
    scheme, and its SessionAuthentication names none — so it falls back to 403.
    Naming one ("Session") gets the honest status code.
    """

    def authenticate_header(self, request):
        return "Session"
