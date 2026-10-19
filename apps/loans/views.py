"""Loans in the app (5.8). Every endpoint answers loans_off unless the flag is on."""

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.models import Membership
from apps.chamas.views import chama_for
from apps.core.idempotency import idempotent
from apps.core.problems import Problem
from apps.core.two_step import ChallengeSentSerializer, code_sent

from . import services
from .models import Guarantee, Loan


def _name(m: Membership) -> str:
    return m.user.display_name or m.member_code


class GuaranteeSerializer(serializers.ModelSerializer):
    guarantor = serializers.SerializerMethodField()
    guarantor_id = serializers.UUIDField(source="guarantor.id")
    mine = serializers.SerializerMethodField()

    class Meta:
        model = Guarantee
        fields = ["id", "guarantor", "guarantor_id", "share_cents", "state", "answered_at",
                  "expires_at", "mine"]

    def get_guarantor(self, g) -> str:
        return _name(g.guarantor)

    def get_mine(self, g) -> bool:
        me = self.context.get("me")
        return bool(me) and g.guarantor_id == me.id


class LoanBalanceSerializer(serializers.Serializer):
    principal_cents = serializers.IntegerField()
    interest_charged_cents = serializers.IntegerField()
    repaid_cents = serializers.IntegerField()
    outstanding_cents = serializers.IntegerField()
    due_now_cents = serializers.IntegerField()
    next = serializers.JSONField(allow_null=True)


class LoanSerializer(serializers.ModelSerializer):
    borrower = serializers.SerializerMethodField()
    borrower_id = serializers.UUIDField(source="borrower.id")
    mine = serializers.SerializerMethodField()
    guarantees = serializers.SerializerMethodField()
    balance = serializers.SerializerMethodField()

    class Meta:
        model = Loan
        fields = ["id", "borrower", "borrower_id", "mine", "amount_cents", "term_months",
                  "purpose", "terms", "schedule", "state", "state_reason", "proposal_id",
                  "disbursed_on", "expires_at", "guarantees", "balance", "created_at"]

    def get_borrower(self, loan) -> str:
        return _name(loan.borrower)

    def get_mine(self, loan) -> bool:
        me = self.context.get("me")
        return bool(me) and loan.borrower_id == me.id

    @extend_schema(responses=GuaranteeSerializer(many=True))
    def get_guarantees(self, loan) -> list[dict]:
        return GuaranteeSerializer(loan.guarantees.select_related("guarantor__user")
                                   .order_by("created_at"), many=True,
                                   context=self.context).data

    @extend_schema(responses=LoanBalanceSerializer)
    def get_balance(self, loan) -> dict:
        return services.balance(loan)


class QuoteReasonSerializer(serializers.Serializer):
    code = serializers.CharField()
    rule = serializers.CharField()
    message = serializers.CharField()


class QuoteSerializer(serializers.Serializer):
    eligible = serializers.BooleanField()
    reasons = QuoteReasonSerializer(many=True)
    savings_cents = serializers.IntegerField()
    limit_cents = serializers.IntegerField()
    terms = serializers.JSONField()
    guarantors_needed = serializers.IntegerField()
    share_cents = serializers.IntegerField()
    schedule = serializers.JSONField()
    total_interest_cents = serializers.IntegerField()
    total_cents = serializers.IntegerField()


class LoanRequestSerializer(serializers.Serializer):
    amount_cents = serializers.IntegerField(min_value=1)
    term_months = serializers.IntegerField(min_value=1)
    purpose = serializers.CharField(max_length=200)
    guarantor_ids = serializers.ListField(child=serializers.UUIDField(), required=False,
                                          default=list)


class ReplaceGuarantorSerializer(serializers.Serializer):
    guarantee_id = serializers.UUIDField()
    membership_id = serializers.UUIDField()


class GuaranteeAnswerSerializer(serializers.Serializer):
    challenge_id = serializers.UUIDField(required=False)
    sms_code = serializers.CharField(required=False, max_length=6)


def _loans():
    return Loan.objects.select_related("chama", "borrower__user")


def _data(loan, me):
    return LoanSerializer(_loans().get(pk=loan.pk), context={"me": me}).data


def _loan_for(request, pk):
    loan = _loans().filter(pk=pk).first()
    if loan is None:
        raise Problem("not_found", "Loan not found.", status.HTTP_404_NOT_FOUND)
    me = require_member(request.user, loan.chama)
    services._require_enabled(loan.chama)
    return loan, me


class LoanListView(APIView):
    @extend_schema(responses=LoanSerializer(many=True))
    def get(self, request, pk):
        chama, me = chama_for(request, pk)
        services._require_enabled(chama)
        for loan in _loans().filter(chama=chama, state__in=[Loan.State.AWAITING_APPROVAL,
                                                            Loan.State.APPROVED]):
            services.sync(loan)
        qs = _loans().filter(chama=chama).order_by("-created_at")[:50]
        return Response(LoanSerializer(qs, many=True, context={"me": me}).data)

    @extend_schema(request=LoanRequestSerializer, responses={201: LoanSerializer})
    @idempotent()
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        s = LoanRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        loan = services.request(chama, me, **s.validated_data)
        return Response(_data(loan, me), status=status.HTTP_201_CREATED)


class LoanQuoteView(APIView):
    @extend_schema(parameters=[OpenApiParameter("amount_cents", int),
                               OpenApiParameter("term_months", int)],
                   responses=QuoteSerializer)
    def get(self, request, pk):
        chama, me = chama_for(request, pk)
        try:
            amount = int(request.query_params.get("amount_cents", 0))
            term = int(request.query_params.get("term_months", 1))
        except ValueError as exc:
            raise Problem("invalid", "Amount and term are whole numbers.") from exc
        return Response(services.quote(chama, me, amount_cents=amount, term_months=term))


class LoanDetailView(APIView):
    @extend_schema(responses=LoanSerializer)
    def get(self, request, pk):
        loan, me = _loan_for(request, pk)
        services.sync(loan)
        return Response(_data(loan, me))


class LoanCancelView(APIView):
    @extend_schema(request=None, responses=LoanSerializer)
    def post(self, request, pk):
        loan, me = _loan_for(request, pk)
        services.cancel(loan, me)
        return Response(_data(loan, me))


class LoanGuarantorView(APIView):
    @extend_schema(request=ReplaceGuarantorSerializer, responses=LoanSerializer)
    def post(self, request, pk):
        loan, me = _loan_for(request, pk)
        s = ReplaceGuarantorSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.replace_guarantor(loan, me, **s.validated_data)
        return Response(_data(loan, me))


def _guarantee_for(request, pk):
    g = Guarantee.objects.select_related("loan__chama", "loan__borrower__user",
                                         "guarantor__user").filter(pk=pk).first()
    if g is None:
        raise Problem("not_found", "Guarantee not found.", status.HTTP_404_NOT_FOUND)
    me = require_member(request.user, g.loan.chama)
    services._require_enabled(g.loan.chama)
    return g, me


class GuaranteeAcceptView(APIView):
    """Without a code: sends one naming the amount and share. With it: accepts."""

    @extend_schema(request=GuaranteeAnswerSerializer,
                   responses={202: ChallengeSentSerializer, 200: LoanSerializer})
    def post(self, request, pk):
        g, me = _guarantee_for(request, pk)
        s = GuaranteeAnswerSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if not s.validated_data.get("sms_code"):
            return code_sent(services.start_accept(g, me), request.user)
        services.accept(g, me, challenge_id=s.validated_data.get("challenge_id"),
                        sms_code=s.validated_data["sms_code"])
        return Response(_data(g.loan, me))


class GuaranteeDeclineView(APIView):
    @extend_schema(request=None, responses=LoanSerializer)
    def post(self, request, pk):
        g, me = _guarantee_for(request, pk)
        services.decline(g, me)
        return Response(_data(g.loan, me))
