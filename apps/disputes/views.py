"""Disputes in the app (5.14)."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.models import Membership
from apps.chamas.views import chama_for
from apps.core.problems import Problem
from apps.core.two_step import ChallengeSentSerializer, code_sent

from . import services
from .models import Dispute, DisputeMessage


def _name(m: Membership) -> str:
    return m.user.display_name or m.member_code


class DisputeMessageSerializer(serializers.ModelSerializer):
    by = serializers.SerializerMethodField()

    class Meta:
        model = DisputeMessage
        fields = ["id", "by", "kind", "text", "round", "at"]

    def get_by(self, m) -> str:
        return _name(m.membership)


class DisputeSerializer(serializers.ModelSerializer):
    raised_by = serializers.SerializerMethodField()
    mine = serializers.SerializerMethodField()
    involved_names = serializers.SerializerMethodField()
    i_am_involved = serializers.SerializerMethodField()
    i_can_decide = serializers.SerializerMethodField()
    decided_by = serializers.SerializerMethodField()
    outcome_member = serializers.SerializerMethodField()
    agreed = serializers.SerializerMethodField()
    officials_needed = serializers.SerializerMethodField()
    messages = serializers.SerializerMethodField()
    receipt = serializers.CharField(source="entry.receipt_code", default=None)

    class Meta:
        model = Dispute
        fields = ["id", "raised_by", "mine", "subject_type", "subject_id", "reason", "text",
                  "officials_only", "involved_names", "i_am_involved", "respond_until",
                  "state", "round", "outcome", "outcome_text", "outcome_member",
                  "outcome_cents", "vote_id", "decided_by", "agreed", "officials_needed",
                  "i_can_decide", "messages", "receipt", "created_at"]

    def _me(self):
        return self.context.get("me")

    def get_raised_by(self, d) -> str:
        return _name(d.raised_by)

    def get_mine(self, d) -> bool:
        me = self._me()
        return bool(me) and d.raised_by_id == me.id

    def get_involved_names(self, d) -> list[str]:
        return [_name(m) for m in Membership.objects.filter(pk__in=d.involved)
                .select_related("user")]

    def get_i_am_involved(self, d) -> bool:
        me = self._me()
        return bool(me) and str(me.id) in d.involved

    def get_decided_by(self, d) -> str:
        return services._rules(d.chama)["decided_by"]

    def get_officials_needed(self, d) -> int:
        return services._rules(d.chama)["officials"]

    def get_outcome_member(self, d) -> str | None:
        return _name(d.outcome_member) if d.outcome_member_id else None

    def get_agreed(self, d) -> int:
        return d.messages.filter(round=d.round, kind=DisputeMessage.Kind.AGREE).count() \
            if d.round else 0

    def get_i_can_decide(self, d) -> bool:
        me = self._me()
        return bool(me) and d.state in Dispute.OPEN and \
            services.deciders(d).filter(pk=me.pk).exists() and not (
                d.state == Dispute.State.DECIDING and d.messages.filter(
                    round=d.round, membership=me,
                    kind__in=[DisputeMessage.Kind.AGREE, DisputeMessage.Kind.DISAGREE])
                .exists())

    @extend_schema(responses=DisputeMessageSerializer(many=True))
    def get_messages(self, d) -> list[dict]:
        return DisputeMessageSerializer(d.messages.select_related("membership__user")
                                        .order_by("at"), many=True).data


def _disputes():
    return Dispute.objects.select_related("chama", "raised_by__user", "outcome_member__user",
                                          "entry")


def _data(d, me):
    return DisputeSerializer(_disputes().get(pk=d.pk), context={"me": me}).data


def _dispute_for(request, pk):
    d = _disputes().filter(pk=pk).first()
    if d is None:
        raise Problem("not_found", "Dispute not found.", status.HTTP_404_NOT_FOUND)
    me = require_member(request.user, d.chama)
    if not services.can_see(d, me):
        raise Problem("not_found", "Dispute not found.", status.HTTP_404_NOT_FOUND)
    return d, me


class RaiseSerializer(serializers.Serializer):
    subject_type = serializers.ChoiceField(choices=Dispute.Subject.choices)
    subject_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    reason = serializers.ChoiceField(choices=Dispute.Reason.choices)
    text = serializers.CharField(max_length=4000)
    officials_only = serializers.BooleanField(required=False, default=False)


class DisputeTextSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=4000, required=False, allow_blank=True,
                                 default="")


class OutcomeSerializer(serializers.Serializer):
    outcome = serializers.ChoiceField(choices=Dispute.Outcome.choices)
    text = serializers.CharField(max_length=4000)
    member_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    amount_cents = serializers.IntegerField(required=False, default=0)


class AgreeSerializer(serializers.Serializer):
    challenge_id = serializers.UUIDField(required=False)
    sms_code = serializers.CharField(required=False, max_length=6)


class DisputeListView(APIView):
    @extend_schema(responses=DisputeSerializer(many=True))
    def get(self, request, pk):
        chama, me = chama_for(request, pk)
        return Response(DisputeSerializer(services.visible(chama, me), many=True,
                                          context={"me": me}).data)

    @extend_schema(request=RaiseSerializer, responses={201: DisputeSerializer})
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        s = RaiseSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = services.raise_dispute(chama, me, **s.validated_data)
        return Response(_data(d, me), status=status.HTTP_201_CREATED)


class DisputeDetailView(APIView):
    @extend_schema(responses=DisputeSerializer)
    def get(self, request, pk):
        d, me = _dispute_for(request, pk)
        return Response(_data(d, me))


class EvidenceSerializer(serializers.Serializer):
    dispute = serializers.UUIDField()
    chama = serializers.CharField()
    subject = serializers.CharField()
    entries = serializers.ListField(child=serializers.DictField())
    messages = serializers.ListField(child=serializers.DictField())


class DisputeEvidenceView(APIView):
    @extend_schema(responses=EvidenceSerializer)
    def get(self, request, pk):
        d, _ = _dispute_for(request, pk)
        return Response(services.evidence(d))


class DisputeRespondView(APIView):
    @extend_schema(request=DisputeTextSerializer, responses=DisputeSerializer)
    def post(self, request, pk):
        d, me = _dispute_for(request, pk)
        s = DisputeTextSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.respond(d, me, text=s.validated_data["text"])
        return Response(_data(d, me))


class DisputeWithdrawView(APIView):
    @extend_schema(request=DisputeTextSerializer, responses=DisputeSerializer)
    def post(self, request, pk):
        d, me = _dispute_for(request, pk)
        s = DisputeTextSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.withdraw(d, me, text=s.validated_data["text"])
        return Response(_data(d, me))


class DisputeEscalateView(APIView):
    @extend_schema(request=DisputeTextSerializer, responses=DisputeSerializer)
    def post(self, request, pk):
        d, me = _dispute_for(request, pk)
        s = DisputeTextSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.escalate(d, me, text=s.validated_data["text"])
        return Response(_data(d, me))


class DisputeOutcomeView(APIView):
    @extend_schema(request=OutcomeSerializer, responses=DisputeSerializer)
    def post(self, request, pk):
        d, me = _dispute_for(request, pk)
        s = OutcomeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.propose_outcome(d, me, **s.validated_data)
        return Response(_data(d, me))


class DisputeAgreeView(APIView):
    """Without a code: sends one naming the outcome. With it: agrees."""

    @extend_schema(request=AgreeSerializer,
                   responses={202: ChallengeSentSerializer, 200: DisputeSerializer})
    def post(self, request, pk):
        d, me = _dispute_for(request, pk)
        s = AgreeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if not s.validated_data.get("sms_code"):
            return code_sent(services.start_agree(d, me), request.user)
        services.agree(d, me, challenge_id=s.validated_data.get("challenge_id"),
                       sms_code=s.validated_data["sms_code"])
        return Response(_data(d, me))


class DisputeDisagreeView(APIView):
    @extend_schema(request=DisputeTextSerializer, responses=DisputeSerializer)
    def post(self, request, pk):
        d, me = _dispute_for(request, pk)
        s = DisputeTextSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.disagree(d, me, text=s.validated_data["text"])
        return Response(_data(d, me))
