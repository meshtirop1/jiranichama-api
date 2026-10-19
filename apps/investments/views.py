"""Investments in the app (4.10)."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.views import chama_for
from apps.core.problems import Problem

from . import services
from .models import Investment, Valuation


class ValuationSerializer(serializers.ModelSerializer):
    recorded_by = serializers.CharField(source="recorded_by.member_code")
    confirmed_by = serializers.CharField(source="confirmed_by.member_code", default=None)
    receipt = serializers.CharField(source="entry.receipt_code", default=None)

    class Meta:
        model = Valuation
        fields = ["id", "value_cents", "as_of", "source", "document_sha256", "recorded_by",
                  "confirmed_by", "confirmed_at", "receipt"]


class InvestmentSerializer(serializers.ModelSerializer):
    value_cents = serializers.SerializerMethodField(
        help_text="The latest confirmed valuation, or the cost when there is none.")
    income_cents = serializers.SerializerMethodField()
    valuations = serializers.SerializerMethodField()
    receipt = serializers.CharField(source="entry.receipt_code")

    class Meta:
        model = Investment
        fields = ["id", "kind", "name", "details", "bought_on", "cost_cents", "purchase_id",
                  "state", "sold_on", "value_cents", "income_cents", "valuations", "receipt",
                  "created_at"]

    def get_value_cents(self, i) -> int:
        v = services.latest_value(i)
        return v.value_cents if v else i.cost_cents

    def get_income_cents(self, i) -> int:
        return services.income_cents(i)

    @extend_schema(responses=ValuationSerializer(many=True))
    def get_valuations(self, i) -> list[dict]:
        return ValuationSerializer(i.valuations.select_related(
            "recorded_by", "confirmed_by", "entry").order_by("-as_of", "-created_at"),
            many=True).data


class PortfolioSerializer(serializers.Serializer):
    held = serializers.IntegerField()
    cost_cents = serializers.IntegerField()
    value_cents = serializers.IntegerField()
    bank_cents = serializers.IntegerField(allow_null=True)
    net_worth_cents = serializers.IntegerField(allow_null=True)
    investments = InvestmentSerializer(many=True)


class RecordInvestmentSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=Investment.Kind.choices)
    name = serializers.CharField(max_length=120)
    details = serializers.CharField(required=False, allow_blank=True, default="")
    bought_on = serializers.DateField()
    cost_cents = serializers.IntegerField(required=False, allow_null=True, default=None)
    purchase_id = serializers.UUIDField(required=False, allow_null=True, default=None)


class ValueSerializer(serializers.Serializer):
    value_cents = serializers.IntegerField(min_value=0)
    as_of = serializers.DateField()
    source = serializers.CharField(max_length=200)
    document = serializers.FileField(required=False)


class IncomeSerializer(serializers.Serializer):
    txn_id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=services.INCOME_KIND_CHOICES)
    note = serializers.CharField(required=False, allow_blank=True, default="")


def _investments():
    return Investment.objects.select_related("chama", "entry")


def _data(inv):
    return InvestmentSerializer(_investments().get(pk=inv.pk)).data


def _investment_for(request, pk):
    inv = _investments().filter(pk=pk).first()
    if inv is None:
        raise Problem("not_found", "Investment not found.", status.HTTP_404_NOT_FOUND)
    return inv, require_member(request.user, inv.chama)


class InvestmentListView(APIView):
    """The portfolio: every member sees what the chama owns and what it is worth."""

    @extend_schema(responses=PortfolioSerializer)
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        qs = _investments().filter(chama=chama).order_by("state", "-bought_on")
        return Response({**services.portfolio(chama),
                         "investments": InvestmentSerializer(qs, many=True).data})

    @extend_schema(request=RecordInvestmentSerializer, responses={201: InvestmentSerializer})
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        s = RecordInvestmentSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        inv = services.record(chama, me, **s.validated_data)
        return Response(_data(inv), status=status.HTTP_201_CREATED)


class ValuationView(APIView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(request=ValueSerializer, responses={201: InvestmentSerializer})
    def post(self, request, pk):
        inv, me = _investment_for(request, pk)
        s = ValueSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.value(inv, me, **s.validated_data)
        return Response(_data(inv), status=status.HTTP_201_CREATED)


class ConfirmValuationView(APIView):
    @extend_schema(request=None, responses=InvestmentSerializer)
    def post(self, request, pk):
        v = Valuation.objects.select_related("investment__chama").filter(pk=pk).first()
        if v is None:
            raise Problem("not_found", "Valuation not found.", status.HTTP_404_NOT_FOUND)
        me = require_member(request.user, v.investment.chama)
        services.confirm(v, me)
        return Response(_data(v.investment))


class IncomeView(APIView):
    @extend_schema(request=IncomeSerializer, responses=InvestmentSerializer)
    def post(self, request, pk):
        inv, me = _investment_for(request, pk)
        s = IncomeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.add_income(inv, me, **s.validated_data)
        return Response(_data(inv))
