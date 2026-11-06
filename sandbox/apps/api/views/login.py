"""
Overrides of oscarapi.views.login, loaded through OSCARAPI_OVERRIDE_MODULES
(classes not defined here, such as LoginView, come from oscarapi).
"""
from oscarapi.views import login as core_views
from rest_framework.throttling import ScopedRateThrottle


class RegistrationView(core_views.RegistrationView):
    """POST /api/register/, throttled per client IP (``register`` rate) so
    accounts cannot be created in bulk"""

    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "register"
