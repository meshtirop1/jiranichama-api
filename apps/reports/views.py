"""Numbers for running the chama (Phase 5)."""

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.views import chama_for

from .analytics import report


class MonthSerializer(serializers.Serializer):
    period = serializers.CharField()
    due_cents = serializers.IntegerField()
    paid_cents = serializers.IntegerField()
    paid_pct = serializers.IntegerField(allow_null=True)
    on_time_cents = serializers.IntegerField()
    late_cents = serializers.IntegerField()
    fines_raised_cents = serializers.IntegerField()
    fines_paid_cents = serializers.IntegerField()
    fines_waived_cents = serializers.IntegerField()


class BehindSerializer(serializers.Serializer):
    member_code = serializers.CharField()
    name = serializers.CharField()
    arrears_cents = serializers.IntegerField()
    fines_cents = serializers.IntegerField()


class MeetingStatSerializer(serializers.Serializer):
    title = serializers.CharField(allow_null=True)
    starts_at = serializers.CharField(allow_null=True)
    attended = serializers.IntegerField()
    expected = serializers.IntegerField()
    attendance_pct = serializers.IntegerField(allow_null=True)


class LoanStatsSerializer(serializers.Serializer):
    running = serializers.IntegerField()
    outstanding_cents = serializers.IntegerField()
    in_arrears = serializers.IntegerField()
    defaulted = serializers.IntegerField()


class MoneySerializer(serializers.Serializer):
    contributed_cents = serializers.IntegerField()
    bank_cents = serializers.IntegerField(allow_null=True)
    investments_cents = serializers.IntegerField()
    net_worth_cents = serializers.IntegerField(allow_null=True)


class AnalyticsSerializer(serializers.Serializer):
    months = MonthSerializer(many=True)
    members = serializers.IntegerField()
    behind = BehindSerializer(many=True)
    behind_count = serializers.IntegerField()
    meetings = MeetingStatSerializer(many=True)
    loans = LoanStatsSerializer(allow_null=True)
    money = MoneySerializer()


class AnalyticsView(APIView):
    @extend_schema(parameters=[OpenApiParameter("months", int)], responses=AnalyticsSerializer)
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        try:
            months = max(1, min(int(request.query_params.get("months", 6)), 24))
        except ValueError:
            months = 6
        return Response(report(chama, months=months))
