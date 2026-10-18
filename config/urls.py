from django.conf import settings
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from apps.core.views import healthz
from apps.staff.site import staff_site

urlpatterns = [
    path("healthz", healthz),
    path("staff/", staff_site.urls),
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/v1/", include("config.api_urls")),
]

if settings.DEBUG and settings.DEV_SMS_FILE:
    from apps.notify.views import dev_sms_outbox

    urlpatterns.append(path("dev/sms/", dev_sms_outbox))
