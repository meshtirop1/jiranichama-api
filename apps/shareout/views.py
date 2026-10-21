"""Year-end share-out in the app. Every member sees the figures; officials prepare them."""

from datetime import date

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.views import chama_for
from apps.core.problems import Problem

from . import services
from .models import ShareOut


class DraftSerializer(serializers.Serializer):
    period_from = serializers.DateField()
    period_to = serializers.DateField()
    label = serializers.CharField(max_length=40, required=False, allow_blank=True)
    return_savings = serializers.BooleanField(required=False)
    keep_pct = serializers.IntegerField(min_value=0, max_value=services.MAX_KEEP_PCT,
                                        required=False)
    weight = serializers.ChoiceField(choices=ShareOut.Weight.choices, required=False)


class ShareOutListView(APIView):
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request, pk):
        chama, me = chama_for(request, pk)
        rows = chama.shareouts.order_by("-created_at")[:10]
        today = date.today()
        return Response({"shareouts": [services.as_dict(x, me) for x in rows],
                         "suggested": {"period_from": f"{today.year - 1}-01-01",
                                       "period_to": f"{today.year - 1}-12-31"},
                         "can_manage": me.is_official})

    @extend_schema(request=DraftSerializer, responses={201: OpenApiTypes.OBJECT})
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        s = DraftSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        so = services.draft(chama, me, **s.validated_data)
        return Response(services.as_dict(so, me), status=status.HTTP_201_CREATED)


def _so_for(request, pk):
    so = ShareOut.objects.select_related("chama", "entry").filter(pk=pk).first()
    if so is None:
        raise Problem("not_found", "Share-out not found.", status.HTTP_404_NOT_FOUND)
    return so, require_member(request.user, so.chama)


class ShareOutDetailView(APIView):
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request, pk):
        so, me = _so_for(request, pk)
        return Response(services.as_dict(so, me))


class ShareOutActionView(APIView):
    """submit (to the members' vote), cancel (a draft), or propose (retry the payments
    after money out is switched on)."""

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    def post(self, request, pk, action):
        so, me = _so_for(request, pk)
        if action == "submit":
            so = services.submit(so, me)
        elif action == "cancel":
            so = services.cancel(so, me)
        elif action == "propose":
            services._official(me)
            services.propose_payments(so.pk, by=me)
        else:
            raise Problem("not_found", "Unknown action.", status.HTTP_404_NOT_FOUND)
        so.refresh_from_db()
        return Response(services.as_dict(so, me))
