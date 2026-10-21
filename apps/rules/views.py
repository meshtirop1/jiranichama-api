"""Rule changes in the app (5.10)."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.views import chama_for
from apps.core.idempotency import idempotent
from apps.core.problems import Problem

from . import proposals
from .models import RuleProposal


class RuleProposalSerializer(serializers.ModelSerializer):
    proposed_by = serializers.SerializerMethodField()
    clauses = serializers.SerializerMethodField(
        help_text="Changed rule-book clauses, before and after, in en and sw.")
    effective_from = serializers.DateField(source="new_version.effective_from", default=None)

    class Meta:
        model = RuleProposal
        fields = ["id", "state", "reason", "proposed_by", "changed", "clauses", "rules",
                  "notice_until", "vote_days", "quorum_pct", "pass_pct", "vote_id",
                  "effective_from", "created_at"]

    def get_proposed_by(self, p) -> str:
        m = p.proposed_by
        return m.user.display_name or m.member_code

    def get_clauses(self, p) -> dict:
        return {lang: proposals.changed_clauses(p.base_version.rules, p.rules, lang)
                for lang in ("en", "sw")}


class ProposeRulesSerializer(serializers.Serializer):
    rules = serializers.JSONField()
    reason = serializers.CharField(max_length=500)


def _proposals():
    return RuleProposal.objects.select_related("proposed_by__user", "base_version",
                                               "new_version")


class RuleProposalListView(APIView):
    @extend_schema(responses=RuleProposalSerializer(many=True))
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        proposals.advance(chama)
        return Response(RuleProposalSerializer(
            _proposals().filter(chama=chama).order_by("-created_at")[:30], many=True).data)

    @extend_schema(request=ProposeRulesSerializer, responses={201: RuleProposalSerializer})
    @idempotent()
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        s = ProposeRulesSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        p = proposals.propose(chama, me, rules=s.validated_data["rules"],
                              reason=s.validated_data["reason"])
        p = _proposals().get(pk=p.pk)
        return Response(RuleProposalSerializer(p).data, status=status.HTTP_201_CREATED)


def _proposal_for(request, pk):
    p = _proposals().select_related("chama").filter(pk=pk).first()
    if p is None:
        raise Problem("not_found", "Proposal not found.", status.HTTP_404_NOT_FOUND)
    return p, require_member(request.user, p.chama)


class RuleProposalDetailView(APIView):
    @extend_schema(responses=RuleProposalSerializer)
    def get(self, request, pk):
        p, _ = _proposal_for(request, pk)
        proposals.advance(p.chama)
        return Response(RuleProposalSerializer(_proposals().get(pk=p.pk)).data)


class RuleProposalWithdrawView(APIView):
    @extend_schema(request=None, responses=RuleProposalSerializer)
    def post(self, request, pk):
        p, me = _proposal_for(request, pk)
        proposals.withdraw(p, me)
        return Response(RuleProposalSerializer(_proposals().get(pk=p.pk)).data)



class CountryRulesView(APIView):
    """What the chama's country asks of it (rules.packs): group size, rules the law
    forbids, and duties, with whether each looks done. Every member sees it."""

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request, pk):
        from .packs import compliance

        chama, _ = chama_for(request, pk)
        lang = request.query_params.get("lang") or request.user.language
        return Response(compliance(chama, lang))
