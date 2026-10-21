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
from .models import Motion, Turnout, Vote, VoteComment


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
    abstain = serializers.SerializerMethodField()
    option_counts = serializers.SerializerMethodField()
    winner = serializers.SerializerMethodField()
    raised_by = serializers.SerializerMethodField()
    comments = serializers.SerializerMethodField()
    result_receipt = serializers.CharField(source="result_entry.receipt_code", default=None)

    class Meta:
        model = Vote
        fields = ["id", "subject", "kind", "question", "details", "options", "state",
                  "closes_at", "secret", "allow_change", "eligible", "quorum_pct",
                  "pass_pct", "quorum_needed", "cast", "yes", "no", "abstain",
                  "option_counts", "winner", "voted", "i_can_vote", "i_voted", "my_choice",
                  "raised_by", "comments", "result_receipt", "created_at"]

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
        """True while the member may vote, or change their vote where that is allowed."""
        me = self._me()
        return (bool(me) and v.state == Vote.State.OPEN and str(me.id) in v.eligible_ids
                and (v.allow_change or not self.get_i_voted(v)))

    def get_my_choice(self, v) -> str | None:
        me = self._me()
        if not me or v.secret:
            return None
        b = services.my_ballot(v, me)
        return b.choice if b else None

    # The tally is shown once the vote has closed, so nobody votes with the crowd.
    def get_yes(self, v) -> int | None:
        return v.yes if v.state != Vote.State.OPEN else None

    def get_no(self, v) -> int | None:
        return v.no if v.state != Vote.State.OPEN else None

    def get_abstain(self, v) -> int | None:
        return v.abstain if v.state != Vote.State.OPEN else None

    def get_option_counts(self, v) -> dict | None:
        return v.option_counts if v.state != Vote.State.OPEN and v.kind == "choice" else None

    def get_winner(self, v) -> int | None:
        return v.winner if v.state != Vote.State.OPEN else None

    def get_raised_by(self, v) -> str | None:
        m = v.raised_by
        return (m.user.display_name or m.member_code) if m else None

    def get_comments(self, v) -> int:
        return VoteComment.objects.filter(vote=v).count()


_CHOICE_HELP = "yes, no or abstain; in a poll the option's number (1 to 6) or abstain"


class BallotSerializer(serializers.Serializer):
    choice = serializers.CharField(max_length=8, help_text=_CHOICE_HELP)
    challenge_id = serializers.UUIDField(required=False)
    code = serializers.RegexField(r"^\d{6}$", required=False)


class ChoiceSerializer(serializers.Serializer):
    choice = serializers.CharField(max_length=8, help_text=_CHOICE_HELP)


class SignedBallotSerializer(serializers.Serializer):
    choice = serializers.CharField(max_length=8, help_text=_CHOICE_HELP)
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


# --- Comments on a vote ----------------------------------------------------------------------

class CommentSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    member_code = serializers.CharField(source="membership.member_code", read_only=True)

    class Meta:
        model = VoteComment
        fields = ["id", "name", "member_code", "text", "at"]
        read_only_fields = ["id", "name", "member_code", "at"]

    def get_name(self, c) -> str:
        return c.membership.user.display_name or c.membership.member_code


class CommentListView(APIView):
    """What members say about a vote. Anyone in the chama reads and writes, while it is
    open."""

    @extend_schema(responses=CommentSerializer(many=True))
    def get(self, request, pk):
        v, _ = _vote_for(request, pk)
        rows = VoteComment.objects.filter(vote=v).select_related("membership__user") \
            .order_by("at")
        return Response(CommentSerializer(rows, many=True).data)

    @extend_schema(request=CommentSerializer, responses={201: CommentSerializer})
    def post(self, request, pk):
        v, me = _vote_for(request, pk)
        c = services.comment(v, me, request.data.get("text", ""))
        return Response(CommentSerializer(c).data, status=status.HTTP_201_CREATED)


# --- Members' motions ------------------------------------------------------------------------

class MotionSerializer(serializers.ModelSerializer):
    raised_by = serializers.SerializerMethodField()
    seconds = serializers.SerializerMethodField()
    seconded_by = serializers.SerializerMethodField()
    i_can_second = serializers.SerializerMethodField()
    mine = serializers.SerializerMethodField()
    vote = serializers.SerializerMethodField()

    class Meta:
        model = Motion
        fields = ["id", "kind", "question", "details", "options", "vote_days", "state",
                  "seconds_needed", "second_by", "seconds", "seconded_by", "i_can_second",
                  "mine", "raised_by", "vote", "created_at"]

    def _me(self):
        return self.context.get("me")

    def get_raised_by(self, m) -> str:
        return m.raised_by.user.display_name or m.raised_by.member_code

    def get_seconds(self, m) -> int:
        return m.seconds.count()

    def get_seconded_by(self, m) -> list[str]:
        return [s.membership.user.display_name or s.membership.member_code
                for s in m.seconds.select_related("membership__user").order_by("at")]

    def get_mine(self, m) -> bool:
        me = self._me()
        return bool(me) and m.raised_by_id == me.id

    def get_i_can_second(self, m) -> bool:
        me = self._me()
        return (bool(me) and m.state == Motion.State.SECONDING and m.raised_by_id != me.id
                and not m.seconds.filter(membership=me).exists())

    def get_vote(self, m) -> dict | None:
        return VoteSerializer(m.vote, context=self.context).data if m.vote_id else None


class NewMotionSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=Vote.Kind.choices, default="yes_no")
    question = serializers.CharField(max_length=300)
    details = serializers.CharField(max_length=4000, required=False, allow_blank=True)
    options = serializers.ListField(child=serializers.CharField(max_length=80),
                                    required=False, max_length=6)
    vote_days = serializers.IntegerField(min_value=1, max_value=14, default=7)


class MotionListView(APIView):
    @extend_schema(responses=MotionSerializer(many=True))
    def get(self, request, pk):
        chama, me = chama_for(request, pk)
        services.expire_motions(chama)
        services.close_due(chama)
        rows = Motion.objects.filter(chama=chama).select_related(
            "raised_by__user", "vote").order_by("-created_at")[:50]
        return Response(MotionSerializer(rows, many=True, context={"me": me}).data)

    @extend_schema(request=NewMotionSerializer, responses={201: MotionSerializer})
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        s = NewMotionSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        m = services.raise_motion(chama, me, **s.validated_data)
        return Response(MotionSerializer(m, context={"me": me}).data,
                        status=status.HTTP_201_CREATED)


def _motion_for(request, pk):
    m = Motion.objects.select_related("chama", "raised_by__user", "vote").filter(pk=pk).first()
    if m is None:
        raise Problem("not_found", "Motion not found.", status.HTTP_404_NOT_FOUND)
    return m, require_member(request.user, m.chama)


class MotionSecondView(APIView):
    @extend_schema(request=None, responses=MotionSerializer)
    def post(self, request, pk):
        m, me = _motion_for(request, pk)
        m = services.second(m, me)
        return Response(MotionSerializer(m, context={"me": me}).data)


class MotionWithdrawView(APIView):
    @extend_schema(request=None, responses=MotionSerializer)
    def post(self, request, pk):
        m, me = _motion_for(request, pk)
        m = services.withdraw_motion(m, me)
        return Response(MotionSerializer(m, context={"me": me}).data)
