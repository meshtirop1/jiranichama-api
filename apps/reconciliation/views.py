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

from . import services


class BankTransactionSerializer(serializers.ModelSerializer):
    narration = serializers.CharField(read_only=True)

    class Meta:
        model = BankTransaction
        fields = ["id", "source", "direction", "amount_cents", "value_date", "narration",
                  "state", "external_ref", "created_at"]


class AssignSerializer(serializers.Serializer):
    membership_id = serializers.UUIDField()
    reason = serializers.CharField(max_length=500)


class NotMeSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class UnmatchedView(APIView):
    @extend_schema(responses=BankTransactionSerializer(many=True))
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        txns = chama.bank_transactions.filter(
            state=BankTransaction.State.UNMATCHED,
            direction=BankTransaction.Direction.CREDIT).order_by("value_date", "created_at")
        return Response(BankTransactionSerializer(txns, many=True).data)


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
