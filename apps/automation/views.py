"""Notices, "what do I owe and how do I pay", Pay now, and the bill shortcut."""

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.views import chama_for
from apps.core.problems import Problem

from . import monthly, reminders
from .models import Notice


def notice_dict(n: Notice) -> dict:
    return {"id": n.id, "kind": n.kind, "text": n.text, "data": n.data, "urgent": n.urgent,
            "chama_id": str(n.chama_id) if n.chama_id else None,
            "chama": n.chama.name if n.chama_id else None, "read": n.read_at is not None,
            "at": n.created_at.isoformat()}


class NoticesView(APIView):
    """The person's notices, newest first, and how many are unread."""

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        qs = Notice.objects.filter(user=request.user).select_related("chama")
        return Response({"unread": qs.filter(read_at__isnull=True).count(),
                         "notices": [notice_dict(n) for n in qs.order_by("-created_at")[:60]]})


class ReadSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(), required=False,
                                help_text="Leave out to mark every notice read.")


class NoticesReadView(APIView):
    @extend_schema(request=ReadSerializer, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        s = ReadSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        qs = Notice.objects.filter(user=request.user, read_at__isnull=True)
        if "ids" in s.validated_data:
            qs = qs.filter(pk__in=s.validated_data["ids"])
        n = qs.update(read_at=timezone.now())
        return Response({"marked": n})


class PayView(APIView):
    """What the member should pay now and how; ``post`` sends the M-Pesa prompt (Pay now)
    where the chama's own paybill allows it."""

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request, pk):
        chama, me = chama_for(request, pk)
        lang = "sw" if request.user.language == "sw" else "en"
        return Response({"owed": reminders.owed(me, timezone.localdate()),
                         "how": reminders.how_to_pay(chama, me, lang)})

    @extend_schema(request=OpenApiTypes.OBJECT, responses={201: OpenApiTypes.OBJECT})
    def post(self, request, pk):
        from apps.feeds import stk

        chama, me = chama_for(request, pk)
        try:
            amount = int(request.data.get("amount_cents") or 0)
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0:
            amount = reminders.owed(me, timezone.localdate())["total_cents"]
        req = stk.start(me, amount)
        return Response({"id": str(req.id), "state": req.state,
                         "amount_cents": req.amount_cents, "phone_hint": req.phone_hint},
                        status=status.HTTP_201_CREATED)


class PayStatusView(APIView):
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request, pk):
        from apps.feeds.models import StkRequest

        req = StkRequest.objects.filter(pk=pk, membership__user=request.user).first()
        if req is None:
            raise Problem("not_found", "Not found.", status.HTTP_404_NOT_FOUND)
        return Response({"id": str(req.id), "state": req.state,
                         "amount_cents": req.amount_cents, "receipt": req.receipt,
                         "result_text": req.result_text})


class BillProposeView(APIView):
    """One tap from the bill notice: propose paying JiraniChama's bill."""

    @extend_schema(request=None, responses={201: OpenApiTypes.OBJECT})
    def post(self, request, pk):
        from apps.billing.models import Bill

        bill = Bill.objects.select_related("chama").filter(pk=pk).first()
        if bill is None:
            raise Problem("not_found", "Bill not found.", status.HTTP_404_NOT_FOUND)
        me = require_member(request.user, bill.chama)
        p = monthly.propose_bill(bill, me)
        return Response({"proposal_id": str(p.id), "state": p.state},
                        status=status.HTTP_201_CREATED)
