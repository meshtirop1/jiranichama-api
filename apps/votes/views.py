"""Members' votes in the app (5.11)."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.views import chama_for
from apps.core.problems import Problem
from apps.core.two_step import ChallengeSentSerializer, code_sent

from . import services
from .models import Ballot, Turnout, Vote


class VoteSerializer(serializers.ModelSerializer):
    cast = serializers.SerializerMethodField()
    quorum_needed = serializers.SerializerMethodField()
    voted = serializers.SerializerMethodField(help_text="Who has voted (never what).")
    i_can_vote = serializers.SerializerMethodField()
    i_voted = serializers.SerializerMethodField()
    my_choice = serializers.SerializerMethodField(
        help_text="Your own choice, in an open vote only; null in a secret vote.")
    yes = serializers.SerializerMethodField()
    no = serializers.SerializerMethodField()
    result_receipt = serializers.CharField(source="result_entry.receipt_code", default=None)

    class Meta:
        model = Vote
        fields = ["id", "subject", "question", "state", "closes_at", "secret", "eligible",
                  "quorum_pct", "pass_pct", "quorum_needed", "cast", "yes", "no", "voted",
                  "i_can_vote", "i_voted", "my_choice", "result_receipt", "created_at"]

    def _me(self):
        return self.context.get("me")

    def get_cast(self, v) -> int:
        return Turnout.objects.filter(vote=v).count()

    def get_quorum_needed(self, v) -> int:
        return services.quorum_needed(v)

    def get_voted(self, v) -> list[str]:
        return sorted(t.membership.user.display_name or t.membership.member_code
                      for t in Turnout.objects.filter(vote=v).select_related("membership__user"))

    def get_i_voted(self, v) -> bool:
        me = self._me()
        return bool(me) and Turnout.objects.filter(vote=v, membership=me).exists()

    def get_i_can_vote(self, v) -> bool:
        me = self._me()
        return (bool(me) and v.state == Vote.State.OPEN and str(me.id) in v.eligible_ids
                and not self.get_i_voted(v))

    def get_my_choice(self, v) -> str | None:
        me = self._me()
        if not me or v.secret:
            return None
        b = Ballot.objects.filter(vote=v, membership=me).first()
        return b.choice if b else None

    # The tally is shown once the vote has closed, so nobody votes with the crowd.
    def get_yes(self, v) -> int | None:
        return v.yes if v.state != Vote.State.OPEN else None

    def get_no(self, v) -> int | None:
        return v.no if v.state != Vote.State.OPEN else None


class BallotSerializer(serializers.Serializer):
    choice = serializers.ChoiceField(choices=Ballot.Choice.choices)
    challenge_id = serializers.UUIDField(required=False)
    code = serializers.RegexField(r"^\d{6}$", required=False)


class ChoiceSerializer(serializers.Serializer):
    choice = serializers.ChoiceField(choices=Ballot.Choice.choices)


class SignedBallotSerializer(serializers.Serializer):
    choice = serializers.ChoiceField(choices=Ballot.Choice.choices)
    device_id = serializers.UUIDField()
    nonce = serializers.CharField(max_length=64)
    payload_hash = serializers.CharField(max_length=64)
    signature = serializers.CharField()


class BallotPayloadSerializer(serializers.Serializer):
    device_id = serializers.UUIDField()
    payload = serializers.JSONField()
    payload_hash = serializers.CharField()


def _vote_for(request, pk):
    v = Vote.objects.select_related("chama", "result_entry").filter(pk=pk).first()
    if v is None:
        raise Problem("not_found", "Vote not found.", status.HTTP_404_NOT_FOUND)
    return v, require_member(request.user, v.chama)


def _out(v, me):
    v.refresh_from_db()
    return VoteSerializer(v, context={"me": me}).data


class VoteListView(APIView):
    @extend_schema(responses=VoteSerializer(many=True))
    def get(self, request, pk):
        chama, me = chama_for(request, pk)
        services.close_due(chama)
        votes = Vote.objects.filter(chama=chama).select_related("result_entry") \
            .order_by("-created_at")[:50]
        return Response(VoteSerializer(votes, many=True, context={"me": me}).data)


class VoteDetailView(APIView):
    @extend_schema(responses=VoteSerializer)
    def get(self, request, pk):
        v, me = _vote_for(request, pk)
        services.close_due(v.chama)
        return Response(_out(v, me))


class BallotView(APIView):
    """By SMS code, two steps: the choice sends a code naming it; the code casts it."""

    @extend_schema(request=BallotSerializer,
                   responses={201: VoteSerializer, 202: ChallengeSentSerializer})
    def post(self, request, pk):
        v, me = _vote_for(request, pk)
        s = BallotSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        if "code" not in d:
            return code_sent(services.start_sms_ballot(v, me, d["choice"]), request.user)
        if "challenge_id" not in d:
            raise Problem("challenge_required", "Send the challenge_id with the code.")
        v = services.cast_sms(v, me, choice=d["choice"], challenge_id=d["challenge_id"],
                              sms_code=d["code"])
        return Response(_out(v, me), status=status.HTTP_201_CREATED)


class BallotSigningPayloadView(APIView):
    @extend_schema(request=ChoiceSerializer, responses=BallotPayloadSerializer)
    def post(self, request, pk):
        v, me = _vote_for(request, pk)
        s = ChoiceSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return Response(services.start_device_ballot(v, me, s.validated_data["choice"]))


class SignedBallotView(APIView):
    @extend_schema(request=SignedBallotSerializer, responses={201: VoteSerializer})
    def post(self, request, pk):
        v, me = _vote_for(request, pk)
        s = SignedBallotSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        v = services.cast_device(v, me, choice=d["choice"], device_id=d["device_id"],
                                 nonce=d["nonce"], payload_hash=d["payload_hash"],
                                 signature_b64=d["signature"])
        return Response(_out(v, me), status=status.HTTP_201_CREATED)
