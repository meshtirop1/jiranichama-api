"""Elections in the app (5.12)."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.views import chama_for
from apps.core.problems import Problem

from . import services
from .models import Election


class ElectionSerializer(serializers.ModelSerializer):
    candidate = serializers.SerializerMethodField()
    candidate_id = serializers.UUIDField(source="candidate.id")
    outgoing = serializers.SerializerMethodField()
    candidate_ready = serializers.SerializerMethodField(
        help_text="The winner's phone key is active, so the role can start.")
    receipt = serializers.CharField(source="role_entry.receipt_code", default=None)

    class Meta:
        model = Election
        fields = ["id", "role", "candidate", "candidate_id", "outgoing", "acting",
                  "term_months", "vote_id", "state", "candidate_ready", "mandate_needed",
                  "mandate_confirmed_at", "receipt", "created_at"]

    def get_candidate(self, e) -> str:
        return e.candidate.user.display_name or e.candidate.member_code

    def get_outgoing(self, e) -> str | None:
        if not e.outgoing_id:
            return None
        return e.outgoing.user.display_name or e.outgoing.member_code

    def get_candidate_ready(self, e) -> bool:
        return services.ready(e.candidate)


class ShortfallSerializer(serializers.Serializer):
    level = serializers.CharField()
    officials = serializers.IntegerField()
    available = serializers.IntegerField()


class ElectionsOutSerializer(serializers.Serializer):
    elections = ElectionSerializer(many=True)
    election_needed = ShortfallSerializer(many=True)


ROLE_CHOICES = [("chair", "chair"), ("treasurer", "treasurer"), ("secretary", "secretary")]


class NominateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=ROLE_CHOICES)
    candidate_id = serializers.UUIDField()
    acting = serializers.BooleanField(required=False, default=False)


def _elections():
    return Election.objects.select_related("chama", "candidate__user", "outgoing__user",
                                           "role_entry")


class ElectionListView(APIView):
    @extend_schema(responses=ElectionsOutSerializer)
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        services.install_ready(chama)
        qs = _elections().filter(chama=chama).order_by("-created_at")[:30]
        return Response({"elections": ElectionSerializer(qs, many=True).data,
                         "election_needed": services.needs_election(chama)})

    @extend_schema(request=NominateSerializer, responses={201: ElectionSerializer})
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        s = NominateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        e = services.nominate(chama, me, **s.validated_data)
        return Response(ElectionSerializer(_elections().get(pk=e.pk)).data,
                        status=status.HTTP_201_CREATED)


class MandateView(APIView):
    @extend_schema(request=None, responses=ElectionSerializer)
    def post(self, request, pk):
        e = _elections().filter(pk=pk).first()
        if e is None:
            raise Problem("not_found", "Election not found.", status.HTTP_404_NOT_FOUND)
        me = require_member(request.user, e.chama)
        services.confirm_mandate(e, me)
        return Response(ElectionSerializer(_elections().get(pk=e.pk)).data)
