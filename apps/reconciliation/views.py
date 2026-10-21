from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.models import Membership
from apps.chamas.views import chama_for
from apps.core.idempotency import idempotent
from apps.core.problems import Problem
from apps.feeds.models import BankTransaction

from . import matching, services
from .models import PayerLink


class BankTransactionSerializer(serializers.ModelSerializer):
    narration = serializers.CharField(read_only=True)
    typed_in = serializers.SerializerMethodField(
        help_text="An official typed this line in; it was not read from the bank's file.")

    class Meta:
        model = BankTransaction
        fields = ["id", "source", "direction", "amount_cents", "value_date", "narration",
                  "state", "external_ref", "typed_in", "created_at"]

    def get_typed_in(self, obj) -> bool:
        return obj.typed_by_id is not None


class AssignSerializer(serializers.Serializer):
    membership_id = serializers.UUIDField()
    reason = serializers.CharField(max_length=500)


class NotMeSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class UnmatchedView(APIView):
    @extend_schema(responses=BankTransactionSerializer(many=True))
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        txns = list(chama.bank_transactions.filter(
            state=BankTransaction.State.UNMATCHED,
            direction=BankTransaction.Direction.CREDIT).order_by("value_date", "created_at"))
        out = BankTransactionSerializer(txns, many=True).data
        # Likely members, with their reasons, for the treasurer to confirm with one tap
        # (smart matching, 2026-10-21).
        for row, txn in zip(out, txns, strict=True):
            row["suggestions"] = [{**s, "reason_text": matching.reason_text(s)}
                                  for s in matching.suggest(txn)]
        return Response(out)


def _txn_for(request, pk) -> tuple[BankTransaction, Membership]:
    txn = BankTransaction.objects.select_related("chama").filter(pk=pk).first()
    if txn is None:
        raise Problem("not_found", "Payment not found.", status.HTTP_404_NOT_FOUND)
    return txn, require_member(request.user, txn.chama)


class AssignView(APIView):
    @extend_schema(request=AssignSerializer, responses=BankTransactionSerializer)
    @idempotent()
    def post(self, request, pk):
        txn, me = _txn_for(request, pk)
        s = AssignSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        member = Membership.objects.filter(pk=s.validated_data["membership_id"],
                                           chama=txn.chama).first()
        if member is None:
            raise Problem("not_found", "Member not found.", status.HTTP_404_NOT_FOUND)
        services.assign(txn, member, by=me, reason=s.validated_data["reason"])
        txn.refresh_from_db()
        return Response(BankTransactionSerializer(txn).data)


class NotMeView(APIView):
    @extend_schema(request=NotMeSerializer, responses=BankTransactionSerializer)
    @idempotent()
    def post(self, request, pk):
        txn, me = _txn_for(request, pk)
        s = NotMeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.not_me(txn, me, reason=s.validated_data.get("reason", ""))
        txn.refresh_from_db()
        return Response(BankTransactionSerializer(txn).data)


class ReconciliationSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["no_statement", "match", "open", "breach"])
    checked_on = serializers.DateField(allow_null=True)
    bank_as_of = serializers.DateField(allow_null=True)
    bank_source = serializers.CharField(allow_null=True, help_text="statement or bank_api")
    bank_cents = serializers.IntegerField(allow_null=True)
    records_cents = serializers.IntegerField(allow_null=True)
    gap_cents = serializers.IntegerField()
    unmatched_cents = serializers.IntegerField()
    unexplained_cents = serializers.IntegerField()
    gap_opened_on = serializers.DateField(allow_null=True)
    days_open = serializers.IntegerField()
    breach_after_days = serializers.IntegerField()
    history = serializers.ListField(child=serializers.DictField())
    statement = serializers.DictField(
        help_text="Freshness: checked_to, every_days, due_on, days_overdue, level "
                  "(0 fresh, 1 treasurer, 2 officials, 3 everyone).")


class ReconciliationView(APIView):
    """Does the bank match the records? Every member sees the same answer (5.5)."""

    @extend_schema(responses=ReconciliationSerializer)
    def get(self, request, pk):
        from .daily import summary

        chama, _ = chama_for(request, pk)
        return Response(summary(chama))


class PayerLinksView(APIView):
    """Numbers that pay for members (smart matching). A member sees their own; officials
    see every one in the chama."""

    @extend_schema(responses=serializers.DictField())
    def get(self, request, pk):
        chama, me = chama_for(request, pk)
        qs = PayerLink.objects.filter(chama=chama).select_related("membership__user",
                                                                  "from_txn")
        if not me.is_official:
            qs = qs.filter(membership=me)
        return Response({"links": [matching.as_dict(x) for x in qs.order_by("-created_at")]})


class PayerLinkAnswerSerializer(serializers.Serializer):
    yes = serializers.BooleanField()


def _link_for(request, pk) -> tuple[PayerLink, Membership]:
    link = (PayerLink.objects.select_related("chama", "membership__user", "from_txn")
            .filter(pk=pk).first())
    if link is None:
        raise Problem("not_found", "Not found.", status.HTTP_404_NOT_FOUND)
    return link, require_member(request.user, link.chama)


class PayerLinkAnswerView(APIView):
    """The member says whether payments from that number should count for them."""

    @extend_schema(request=PayerLinkAnswerSerializer, responses=serializers.DictField())
    def post(self, request, pk):
        link, me = _link_for(request, pk)
        s = PayerLinkAnswerSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return Response(matching.as_dict(matching.answer(link, me,
                                                         yes=s.validated_data["yes"])))


class PayerLinkEndView(APIView):
    @extend_schema(request=None, responses=serializers.DictField())
    def post(self, request, pk):
        link, me = _link_for(request, pk)
        return Response(matching.as_dict(matching.end(link, me)))
