from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.models import Chama

from . import services, smile


class StartSerializer(serializers.Serializer):
    country = serializers.CharField(max_length=2)
    id_type = serializers.CharField(max_length=40)
    id_number = serializers.CharField(max_length=30)
    first_name = serializers.CharField(max_length=60)
    last_name = serializers.CharField(max_length=60)
    paid_by = serializers.UUIDField(required=False, allow_null=True,
                                    help_text="The chama that pays for the check.")


def _me(user) -> dict:
    c = services.latest(user)
    return {
        "available": smile.available(),
        "status": c.status if c else "not_started",
        "id_types": smile.ID_TYPES,
        "last4": c.last4 if c else None,
        "name_matches": c.name_matches if c else None,
        "checked_at": c.decided_at.isoformat() if c and c.decided_at else None,
    }


class MyIdentityView(APIView):
    """The signed-in person's ID check, and starting one."""

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(_me(request.user))

    @extend_schema(request=StartSerializer, responses={201: OpenApiTypes.OBJECT})
    def post(self, request):
        s = StartSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        paid_by = Chama.objects.filter(pk=d["paid_by"]).first() if d.get("paid_by") else None
        services.start(request.user, country=d["country"].upper(), id_type=d["id_type"],
                       id_number=d["id_number"], first_name=d["first_name"],
                       last_name=d["last_name"], paid_by=paid_by)
        return Response(_me(request.user), status=status.HTTP_201_CREATED)
