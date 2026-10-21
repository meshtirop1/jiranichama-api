"""Fines and waivers in the app (5.9)."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.views import chama_for
from apps.core.problems import Problem
from apps.core.two_step import ChallengeSentSerializer, code_sent

from . import waivers
from .models import Fine, Waiver


class WaiverSerializer(serializers.ModelSerializer):
    approvals = serializers.SerializerMethodField()
    i_can_decide = serializers.SerializerMethodField()

    class Meta:
        model = Waiver
        fields = ["id", "reason", "state", "state_reason", "needs_officials", "needs_vote",
                  "deadline_at", "approvals", "i_can_decide", "created_at"]

    def get_approvals(self, w) -> int:
        return sum(1 for d in w.decisions.all() if d.decision == "approve")

    def get_i_can_decide(self, w) -> bool:
        me = self.context.get("me")
        return (bool(me) and w.state == Waiver.State.REQUESTED
                and waivers.deciders(w).filter(pk=me.pk).exists()
                and not any(d.membership_id == me.id for d in w.decisions.all()))


class FineSerializer(serializers.ModelSerializer):
    member = serializers.SerializerMethodField()
    member_id = serializers.UUIDField(source="membership.id")
    mine = serializers.SerializerMethodField()
    outstanding_cents = serializers.IntegerField()
    cause = serializers.CharField(source="raised_entry.decision.cause", default=None)
    receipt = serializers.CharField(source="raised_entry.receipt_code")
    waiver = serializers.SerializerMethodField()

    class Meta:
        model = Fine
        fields = ["id", "member", "member_id", "mine", "rule_ref", "period", "amount_cents",
                  "paid_cents", "outstanding_cents", "state", "cause", "receipt", "waiver",
                  "created_at"]

    def get_member(self, f) -> str:
        return f.membership.user.display_name or f.membership.member_code

    def get_mine(self, f) -> bool:
        me = self.context.get("me")
        return bool(me) and f.membership_id == me.id

    @extend_schema(responses=WaiverSerializer(allow_null=True))
    def get_waiver(self, f) -> dict | None:
        # Prefetched newest first by _fines(): no query per fine.
        ws = list(f.waivers.all())
        w = ws[0] if ws else None
        return WaiverSerializer(w, context=self.context).data if w else None


def _fines():
    from django.db.models import Prefetch

    from .models import Waiver

    return (Fine.objects.select_related("membership__user", "raised_entry", "chama")
            .prefetch_related(Prefetch("waivers", queryset=Waiver.objects.order_by(
                "-created_at").prefetch_related("decisions"))))


class FineListView(APIView):
    """Every fine in the chama: members see who was fined, why, and every waiver."""

    @extend_schema(responses=FineSerializer(many=True))
    def get(self, request, pk):
        chama, me = chama_for(request, pk)
        qs = _fines().filter(chama=chama).order_by("-created_at")[:100]
        return Response(FineSerializer(qs, many=True, context={"me": me}).data)


class WaiverRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


class WaiverAnswerSerializer(serializers.Serializer):
    challenge_id = serializers.UUIDField(required=False)
    sms_code = serializers.CharField(required=False, max_length=6)


class WaiverDeclineSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True,
                                   default="")


def _fine_data(fine, me):
    return FineSerializer(_fines().get(pk=fine.pk), context={"me": me}).data


class WaiverRequestView(APIView):
    @extend_schema(request=WaiverRequestSerializer, responses={201: FineSerializer})
    def post(self, request, pk):
        fine = _fines().filter(pk=pk).first()
        if fine is None:
            raise Problem("not_found", "Fine not found.", status.HTTP_404_NOT_FOUND)
        me = require_member(request.user, fine.chama)
        s = WaiverRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        waivers.request(fine, me, reason=s.validated_data["reason"])
        return Response(_fine_data(fine, me), status=status.HTTP_201_CREATED)


def _waiver_for(request, pk):
    w = Waiver.objects.select_related("fine__chama", "fine__membership__user").filter(pk=pk) \
        .first()
    if w is None:
        raise Problem("not_found", "Waiver not found.", status.HTTP_404_NOT_FOUND)
    return w, require_member(request.user, w.fine.chama)


class WaiverApproveView(APIView):
    """Without a code: sends one naming the member and amount. With it: approves."""

    @extend_schema(request=WaiverAnswerSerializer,
                   responses={202: ChallengeSentSerializer, 200: FineSerializer})
    def post(self, request, pk):
        w, me = _waiver_for(request, pk)
        s = WaiverAnswerSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if not s.validated_data.get("sms_code"):
            return code_sent(waivers.start_approve(w, me), request.user)
        waivers.approve(w, me, challenge_id=s.validated_data.get("challenge_id"),
                        sms_code=s.validated_data["sms_code"])
        return Response(_fine_data(w.fine, me))


class WaiverDeclineView(APIView):
    @extend_schema(request=WaiverDeclineSerializer, responses=FineSerializer)
    def post(self, request, pk):
        w, me = _waiver_for(request, pk)
        s = WaiverDeclineSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        waivers.decline(w, me, reason=s.validated_data["reason"])
        return Response(_fine_data(w.fine, me))
