"""
Sign in / sign out of the mobile app with DRF tokens.
POST /api/auth/token/ is routed in sandbox/urls.py, /api/auth/logout/ in
apps/api/urls.py.
"""
from rest_framework import permissions, status, views
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.api.authentication import ExpiringTokenAuthentication, is_expired


class ThrottledObtainAuthToken(ObtainAuthToken):
    """The token for ``username`` (the email) and ``password``; attempts are
    throttled per client IP (``login`` rate) against password guessing"""

    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "login"

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token, _ = Token.objects.get_or_create(user=user)
        # the token kept from an earlier sign in may have expired
        if is_expired(token):
            token.delete()
            token = Token.objects.create(user=user)
        return Response({"token": token.key})


class Logout(views.APIView):
    """POST: deletes the token sent, which stops working on every device it
    was copied to (204). 401 without a valid token."""

    authentication_classes = (ExpiringTokenAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request):
        request.auth.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
