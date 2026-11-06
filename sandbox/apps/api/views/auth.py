"""
Sign in / sign out of the mobile app with DRF tokens.
POST /api/auth/token/ is routed in sandbox/urls.py, /api/auth/logout/ in
apps/api/urls.py.
"""
from rest_framework import authentication, permissions, status, views
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle


class ThrottledObtainAuthToken(ObtainAuthToken):
    """The token for ``username`` (the email) and ``password``; attempts are
    throttled per client IP (``login`` rate) against password guessing"""

    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "login"


class Logout(views.APIView):
    """POST: deletes the token sent, which stops working on every device it
    was copied to (204). 401 without a valid token."""

    authentication_classes = (authentication.TokenAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request):
        request.auth.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
