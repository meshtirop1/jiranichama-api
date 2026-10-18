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
