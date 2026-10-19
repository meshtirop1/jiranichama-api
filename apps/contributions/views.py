from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.models import Membership
from apps.chamas.views import chama_for
from apps.core.idempotency import idempotent
from apps.core.problems import Problem
from apps.core.two_step import ChallengeSentSerializer, ConfirmSerializer, code_sent
from apps.feeds.models import BankTransaction

from . import services
from .models import CashReceipt, OpeningBalance, OpeningBalanceLine


class OpeningLineSerializer(serializers.ModelSerializer):
    membership_id = serializers.UUIDField()
    member_code = serializers.CharField(source="membership.member_code", read_only=True)
    objections = serializers.SerializerMethodField()

    class Meta:
        model = OpeningBalanceLine
        fields = ["id", "membership_id", "member_code", "savings_cents", "fines_cents",
                  "objections"]

    def get_objections(self, obj) -> list[str]:
        return [o.reason for o in obj.objections.all()]


class OpeningSerializer(serializers.Serializer):
    state = serializers.CharField()
    proposed_by = serializers.CharField(allow_null=True)
    confirmed_by = serializers.CharField(allow_null=True)
    objection_until = serializers.DateTimeField(allow_null=True)
    lines = OpeningLineSerializer(many=True)


class OpeningSaveSerializer(serializers.Serializer):
    lines = serializers.ListField(child=serializers.DictField())


class ObjectionSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


def _name(user):
    return (user.display_name or user.phone_hint) if user else None


def _opening_data(ob: OpeningBalance | None) -> dict:
    if ob is None:
        return {"state": "none", "proposed_by": None, "confirmed_by": None,
                "objection_until": None, "lines": []}
    return {
        "state": ob.state, "proposed_by": _name(ob.proposed_by),
        "confirmed_by": _name(ob.confirmed_by), "objection_until": ob.objection_until,
        "lines": OpeningLineSerializer(
            ob.lines.select_related("membership").prefetch_related("objections")
            .order_by("membership__member_code"), many=True).data,
    }


class OpeningView(APIView):
    @extend_schema(responses=OpeningSerializer)
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        return Response(_opening_data(OpeningBalance.objects.filter(chama=chama).first()))

    @extend_schema(request=OpeningSaveSerializer, responses=OpeningSerializer)
    def put(self, request, pk):
        chama, me = chama_for(request, pk)
        s = OpeningSaveSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return Response(_opening_data(services.save_opening(chama, me, s.validated_data["lines"])))


class OpeningConfirmView(APIView):
    @extend_schema(request=ConfirmSerializer,
                   responses={200: OpeningSerializer, 202: ChallengeSentSerializer})
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        ob = OpeningBalance.objects.filter(chama=chama).first()
        if ob is None:
            raise Problem("nothing_to_confirm", "There are no opening balances to confirm.",
                          status.HTTP_409_CONFLICT)
        s = ConfirmSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if "code" not in s.validated_data:
            return code_sent(services.start_confirm_opening(ob, me), request.user)
        ob = services.confirm_opening(ob, me, challenge_id=s.validated_data["challenge_id"],
                                      sms_code=s.validated_data["code"])
        return Response(_opening_data(ob))


class OpeningObjectView(APIView):
    @extend_schema(request=ObjectionSerializer, responses={201: None})
    def post(self, request, pk):
        line = OpeningBalanceLine.objects.select_related("opening__chama").filter(pk=pk).first()
        if line is None:
            raise Problem("not_found", "Not found.", status.HTTP_404_NOT_FOUND)
        me = require_member(request.user, line.opening.chama)
        s = ObjectionSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.object_to_opening(line, me, s.validated_data["reason"])
        return Response(status=status.HTTP_201_CREATED)


class CashReceiptSerializer(serializers.ModelSerializer):
    member_code = serializers.CharField(source="membership.member_code", read_only=True)
    membership_id = serializers.UUIDField(read_only=True)
    overdue = serializers.SerializerMethodField()

    class Meta:
        model = CashReceipt
        fields = ["id", "membership_id", "member_code", "amount_cents", "received_on", "state",
                  "declined_reason", "overdue", "created_at"]

    def get_overdue(self, obj) -> bool:
        return services.is_overdue(obj)


class RecordCashSerializer(serializers.Serializer):
    membership_id = serializers.UUIDField()
    amount_cents = serializers.IntegerField(min_value=1)
    received_on = serializers.DateField(required=False)


class DeclineSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class BankCashSerializer(serializers.Serializer):
    receipt_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1)


class CashListView(APIView):
    @extend_schema(responses=CashReceiptSerializer(many=True))
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        receipts = chama.cash_receipts.select_related("membership").order_by("-created_at")
        return Response(CashReceiptSerializer(receipts, many=True).data)

    @extend_schema(request=RecordCashSerializer, responses={201: CashReceiptSerializer})
    @idempotent()
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        s = RecordCashSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        member = Membership.objects.filter(pk=s.validated_data["membership_id"],
                                           chama=chama).first()
        if member is None:
            raise Problem("invalid_member", "That member is not in this chama.")
        r = services.record_cash(chama, me, member, amount_cents=s.validated_data["amount_cents"],
                                 received_on=s.validated_data.get("received_on",
                                                                  timezone.localdate()))
        return Response(CashReceiptSerializer(r).data, status=status.HTTP_201_CREATED)


def _receipt_for(request, pk) -> tuple[CashReceipt, Membership]:
    r = CashReceipt.objects.select_related("chama", "membership").filter(pk=pk).first()
    if r is None:
        raise Problem("not_found", "Not found.", status.HTTP_404_NOT_FOUND)
    return r, require_member(request.user, r.chama)


class CashConfirmView(APIView):
    @extend_schema(request=ConfirmSerializer,
                   responses={200: CashReceiptSerializer, 202: ChallengeSentSerializer})
    def post(self, request, pk):
        r, me = _receipt_for(request, pk)
        s = ConfirmSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if "code" not in s.validated_data:
            return code_sent(services.start_confirm_cash(r, me), request.user)
        r = services.confirm_cash(r, me, challenge_id=s.validated_data["challenge_id"],
                                  sms_code=s.validated_data["code"])
        return Response(CashReceiptSerializer(r).data)


class CashDeclineView(APIView):
    @extend_schema(request=DeclineSerializer, responses=CashReceiptSerializer)
    def post(self, request, pk):
        r, me = _receipt_for(request, pk)
        s = DeclineSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return Response(CashReceiptSerializer(
            services.decline_cash(r, me, s.validated_data.get("reason", ""))).data)


class BankCashView(APIView):
    @extend_schema(request=BankCashSerializer, responses={200: None})
    @idempotent()
    def post(self, request, pk):
        txn = BankTransaction.objects.select_related("chama").filter(pk=pk).first()
        if txn is None:
            raise Problem("not_found", "Payment not found.", status.HTTP_404_NOT_FOUND)
        me = require_member(request.user, txn.chama)
        s = BankCashSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.bank_cash(txn, me, s.validated_data["receipt_ids"])
        return Response({"state": "recorded"})
