"""Support access in the app (5.16): officials give, confirm and revoke it."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.views import chama_for
from apps.core.problems import Problem

from . import support
from .models import SupportGrant


class SupportGrantSerializer(serializers.ModelSerializer):
    requested_by = serializers.CharField(source="requested_by.member_code")
    confirmed_by = serializers.CharField(source="confirmed_by.member_code", default=None)

    class Meta:
        model = SupportGrant
        fields = ["id", "areas", "hours", "reason", "requested_by", "confirmed_by", "state",
                  "starts_at", "ends_at", "created_at"]


class SupportRequestSerializer(serializers.Serializer):
    areas = serializers.ListField(child=serializers.ChoiceField(
        choices=SupportGrant.Area.choices))
    hours = serializers.IntegerField(required=False, default=72)
    reason = serializers.CharField(max_length=300)


def _grants():
    return SupportGrant.objects.select_related("chama", "requested_by", "confirmed_by")


class SupportGrantListView(APIView):
    """Support access for this chama: every member sees whether support can read, and
    until when."""

    @extend_schema(responses=SupportGrantSerializer(many=True))
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        support.expire_due()
        qs = _grants().filter(chama=chama).order_by("-created_at")[:10]
        return Response(SupportGrantSerializer(qs, many=True).data)

    @extend_schema(request=SupportRequestSerializer, responses={201: SupportGrantSerializer})
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        s = SupportRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        g = support.request(chama, me, **s.validated_data)
        return Response(SupportGrantSerializer(_grants().get(pk=g.pk)).data,
                        status=status.HTTP_201_CREATED)


def _grant_for(request, pk):
    g = _grants().filter(pk=pk).first()
    if g is None:
        raise Problem("not_found", "Not found.", status.HTTP_404_NOT_FOUND)
    return g, require_member(request.user, g.chama)


class SupportConfirmView(APIView):
    @extend_schema(request=None, responses=SupportGrantSerializer)
    def post(self, request, pk):
        g, me = _grant_for(request, pk)
        support.confirm(g, me)
        return Response(SupportGrantSerializer(_grants().get(pk=g.pk)).data)


class SupportRevokeView(APIView):
    @extend_schema(request=None, responses=SupportGrantSerializer)
    def post(self, request, pk):
        g, me = _grant_for(request, pk)
        support.revoke(g, me)
        return Response(SupportGrantSerializer(_grants().get(pk=g.pk)).data)
