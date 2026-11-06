"""
DRF token authentication with an expiry, used by every API view
(DEFAULT_AUTHENTICATION_CLASSES and the views that set their own classes).

A token unused for API_TOKEN_TTL_DAYS days stops working: it is deleted and
the request answers 401 "Token has expired.", so the app signs the user out.
Using a token moves its date forward (at most once a day), so an active user
stays signed in. API_TOKEN_TTL_DAYS = 0 turns the expiry off.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token

# a used token is moved forward at most this often (one write a day)
REFRESH_EVERY = timedelta(days=1)


def token_ttl():
    """The expiry delay, None when the expiry is off"""
    days = getattr(settings, "API_TOKEN_TTL_DAYS", 0)
    return timedelta(days=days) if days > 0 else None


def is_expired(token):
    ttl = token_ttl()
    return ttl is not None and timezone.now() - token.created > ttl


class ExpiringTokenAuthentication(TokenAuthentication):
    def authenticate_credentials(self, key):
        user, token = super().authenticate_credentials(key)
        if is_expired(token):
            token.delete()
            raise exceptions.AuthenticationFailed("Token has expired.")
        now = timezone.now()
        if token_ttl() is not None and now - token.created > REFRESH_EVERY:
            Token.objects.filter(pk=token.pk).update(created=now)
        return user, token
