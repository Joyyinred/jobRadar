"""
Logging in: Django's own users and sessions, through three small endpoints.

    GET  /api/auth/me/      -> {"user": {"username": "joy"}} or {"user": null}
                               (also hands out the csrftoken cookie)
    POST /api/auth/login/   {"username", "password"} -> {"user": {...}}
    POST /api/auth/logout/  -> {"user": null}

Why sessions and not JWT: the page and the API are one origin (Vite forwards
/api to Django; in production they sit behind one domain), which is exactly
what session cookies are for. The cookie is HttpOnly — page JavaScript can't
read it, so a script injected into the page can't steal the login — and
logging out deletes the session on the server, effective at once. A JWT kept
in localStorage is readable by any script on the page and stays valid until
it expires. Django ships users, password hashing, sessions and CSRF
protection; JWT would mean a library plus our own expiry/refresh/revocation.

The cost of cookies: CSRF. A browser sends our cookie with ANY request to us,
including one made by a form on another site. So every write must also carry
the csrftoken (cookie value -> X-CSRFToken header), which another site can't
read. DRF checks it for every logged-in write; login() checks it itself.

No sign-up: one user so far, made with `manage.py createsuperuser`.
"""
from django.contrib.auth import authenticate, login as django_login, logout as django_logout
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from .authentication import SessionAuthentication
from .exceptions import ValidationError


def me_body(user):
    return {"user": {"username": user.username} if user.is_authenticated else None}


@ensure_csrf_cookie  # the page calls this first, so it has a csrftoken before any POST
@api_view(["GET"])
@permission_classes([AllowAny])
def me(request):
    return Response(me_body(request.user))


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=128, trim_whitespace=False)


class LoginThrottle(AnonRateThrottle):
    """At most "login" (settings: 5/min) attempts per IP address while not
    logged in — slows down guessing passwords from 1000s a minute to 5."""

    scope = "login"


@api_view(["POST"])
@permission_classes([AllowAny])  # you're not logged in yet, by definition
@throttle_classes([LoginThrottle])
def login(request):
    # DRF only checks CSRF for requests that are already logged in, so an
    # anonymous login POST would go unchecked — and another site could log
    # your browser into an account of THEIR choosing ("login CSRF"). Check it.
    SessionAuthentication().enforce_csrf(request)

    data = LoginSerializer(data=request.data)
    data.is_valid(raise_exception=True)
    user = authenticate(request, **data.validated_data)
    if user is None:
        # One message for "no such user" and "wrong password": telling them
        # apart would let anyone find out which usernames exist.
        raise ValidationError("BAD_LOGIN", "Wrong username or password.")
    django_login(request, user)  # new session (new id: no session fixation), cookie set
    return Response(me_body(user))


@api_view(["POST"])
@permission_classes([AllowAny])  # logging out twice is fine
def logout(request):
    django_logout(request)  # deletes the session row: that cookie is dead now
    return Response(me_body(request.user))
