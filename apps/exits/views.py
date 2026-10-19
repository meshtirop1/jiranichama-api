"""Leaving and deaths in the app (5.13)."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.models import Membership
from apps.chamas.views import chama_for
from apps.core.problems import Problem
from apps.core.two_step import ChallengeSentSerializer, code_sent

from . import services
from .models import Exit


class ExitQuoteSerializer(serializers.Serializer):
    savings_cents = serializers.IntegerField()
    fines_cents = serializers.IntegerField()
    loan_cents = serializers.IntegerField()
    deduction_pct = serializers.IntegerField()
    deduction_cents = serializers.IntegerField()
    settlement_cents = serializers.IntegerField()
    shortfall_cents = serializers.IntegerField()
    notice_days = serializers.IntegerField()
    exit_on = serializers.DateField()
    death_benefit_cents = serializers.IntegerField()


class ExitSerializer(serializers.ModelSerializer):
    member = serializers.SerializerMethodField()
    member_id = serializers.UUIDField(source="membership.id")
    mine = serializers.SerializerMethodField()
    receipt = serializers.CharField(source="entry.receipt_code")
    settlement_state = serializers.CharField(source="proposal.state", default=None)

    class Meta:
        model = Exit
        fields = ["id", "member", "member_id", "mine", "kind", "exit_on", "quote", "state",
                  "proposal_id", "settlement_state", "benefit_proposal_id", "died_on",
                  "next_of_kin_name", "receipt", "created_at"]

    def get_member(self, e) -> str:
        return e.membership.user.display_name or e.membership.member_code

    def get_mine(self, e) -> bool:
        me = self.context.get("me")
        return bool(me) and e.membership_id == me.id


def _exits():
    return Exit.objects.select_related("membership__user", "entry", "proposal", "chama")


class ExitListView(APIView):
    """Every exit and death in the chama, with the settlement calculation."""

    @extend_schema(responses=ExitSerializer(many=True))
    def get(self, request, pk):
        chama, me = chama_for(request, pk)
        services.settle_due(chama)
        qs = _exits().filter(chama=chama).order_by("-created_at")[:50]
        return Response(ExitSerializer(qs, many=True, context={"me": me}).data)


class ExitQuoteView(APIView):
    @extend_schema(responses=ExitQuoteSerializer)
    def get(self, request, pk):
        _, me = chama_for(request, pk)
        return Response(services.quote(me))


class LeaveSerializer(serializers.Serializer):
    challenge_id = serializers.UUIDField(required=False)
    sms_code = serializers.CharField(required=False, max_length=6)


class LeaveView(APIView):
    """Without a code: one is sent naming what the member gets back. With it: notice
    starts."""

    @extend_schema(request=LeaveSerializer,
                   responses={202: ChallengeSentSerializer, 201: ExitSerializer})
    def post(self, request, pk):
        _, me = chama_for(request, pk)
        s = LeaveSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if not s.validated_data.get("sms_code"):
            return code_sent(services.start_leave(me), request.user)
        e = services.leave(me, challenge_id=s.validated_data.get("challenge_id"),
                           sms_code=s.validated_data["sms_code"])
        return Response(ExitSerializer(_exits().get(pk=e.pk), context={"me": me}).data,
                        status=status.HTTP_201_CREATED)


class ExitCancelView(APIView):
    @extend_schema(request=None, responses=ExitSerializer)
    def post(self, request, pk):
        e = _exits().filter(pk=pk).first()
        if e is None:
            raise Problem("not_found", "Not found.", status.HTTP_404_NOT_FOUND)
        me = require_member(request.user, e.chama)
        services.cancel(e, me)
        return Response(ExitSerializer(_exits().get(pk=e.pk), context={"me": me}).data)


class DeathSerializer(serializers.Serializer):
    died_on = serializers.DateField()
    next_of_kin_name = serializers.CharField(max_length=120)
    next_of_kin_phone = serializers.CharField(max_length=20)
    document = serializers.FileField()


class DeathView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(request=DeathSerializer, responses={201: ExitSerializer})
    def post(self, request, pk):
        member = Membership.objects.filter(pk=pk).select_related("chama", "user").first()
        if member is None:
            raise Problem("not_found", "Member not found.", status.HTTP_404_NOT_FOUND)
        me = require_member(request.user, member.chama)
        s = DeathSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        e = services.record_death(member.chama, me, member, **s.validated_data)
        return Response(ExitSerializer(_exits().get(pk=e.pk), context={"me": me}).data,
                        status=status.HTTP_201_CREATED)
