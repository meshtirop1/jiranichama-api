"""Referrals in the app (4.10)."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.views import chama_for
from apps.core.problems import Problem

from . import services
from .models import Partner, Referral


class PartnerSerializer(serializers.ModelSerializer):
    regulator_name = serializers.CharField(source="get_regulator_display")

    class Meta:
        model = Partner
        fields = ["id", "name", "regulator", "regulator_name", "licence_number", "product",
                  "description"]


class ReferralSerializer(serializers.ModelSerializer):
    partner = PartnerSerializer()
    contact = serializers.CharField(source="contact.member_code")
    receipt = serializers.CharField(source="consent_entry.receipt_code", default=None)

    class Meta:
        model = Referral
        fields = ["id", "partner", "contact", "reason", "vote_id", "state", "receipt",
                  "sent_at", "created_at"]


class ProposeReferralSerializer(serializers.Serializer):
    partner_id = serializers.UUIDField()
    reason = serializers.CharField(max_length=300)
    contact_id = serializers.UUIDField(required=False, allow_null=True, default=None)


class PartnerListView(APIView):
    @extend_schema(responses=PartnerSerializer(many=True))
    def get(self, request):
        return Response(PartnerSerializer(Partner.objects.filter(active=True)
                                          .order_by("product", "name"), many=True).data)


def _referrals():
    return Referral.objects.select_related("partner", "contact", "consent_entry", "chama")


class ReferralListView(APIView):
    @extend_schema(responses=ReferralSerializer(many=True))
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        qs = _referrals().filter(chama=chama).order_by("-created_at")
        return Response(ReferralSerializer(qs, many=True).data)

    @extend_schema(request=ProposeReferralSerializer, responses={201: ReferralSerializer})
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        s = ProposeReferralSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        r = services.propose(chama, me, **s.validated_data)
        return Response(ReferralSerializer(_referrals().get(pk=r.pk)).data,
                        status=status.HTTP_201_CREATED)


class ReferralWithdrawView(APIView):
    @extend_schema(request=None, responses=ReferralSerializer)
    def post(self, request, pk):
        r = _referrals().filter(pk=pk).first()
        if r is None:
            raise Problem("not_found", "Referral not found.", status.HTTP_404_NOT_FOUND)
        me = require_member(request.user, r.chama)
        services.withdraw(r, me)
        return Response(ReferralSerializer(_referrals().get(pk=r.pk)).data)
