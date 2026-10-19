"""JWT authentication that also notes when a person last used the app.

``User.last_app_at`` decides who gets SMS receipts: members who have not opened the app
in ``RECEIPT_SMS_IF_AWAY_DAYS`` days get their payment receipts by SMS (5.3 step 8);
members who use the app see them there. Written at most once an hour per person.
"""

from datetime import timedelta

from django.utils import timezone
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import User

TOUCH_EVERY = timedelta(hours=1)


class AppJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            user = result[0]
            now = timezone.now()
            if user.last_app_at is None or now - user.last_app_at > TOUCH_EVERY:
                User.objects.filter(pk=user.pk).update(last_app_at=now)
                user.last_app_at = now
        return result


try:
    from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme

    class AppJWTScheme(SimpleJWTScheme):
        """The OpenAPI schema describes it as the plain bearer JWT it is."""

        target_class = "apps.accounts.authentication.AppJWTAuthentication"
        name = "jwtAuth"
except ImportError:  # pragma: no cover - drf-spectacular always installed here
    pass
