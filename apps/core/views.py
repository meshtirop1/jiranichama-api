from django.db import connection
from django.http import JsonResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import countries


def healthz(request):
    with connection.cursor() as cur:
        cur.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


class CountriesView(APIView):
    """The countries and languages a person can choose when they sign up. Countries that
    are not open yet are listed with ``open: false`` so the app can show them as coming."""

    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response({
            "countries": [countries.as_dict(c) for c in countries.COUNTRIES.values()],
            "languages": [{"code": k, "name": v, "translated": k in countries.TRANSLATED}
                          for k, v in countries.LANGUAGES.items()],
            "default": countries.DEFAULT_COUNTRY,
        })
