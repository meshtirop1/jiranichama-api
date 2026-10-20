from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.views import chama_for

from . import services
from .models import Bill


def as_dict(b: Bill) -> dict:
    return {"id": str(b.id), "period": b.period, "currency": b.currency,
            "active_members": b.active_members,
            "price_per_member_cents": b.price_per_member_cents,
            "members_cents": b.members_cents, "id_checks": b.id_checks,
            "id_checks_cents": b.id_checks_cents, "total_cents": b.total_cents,
            "free_reason": b.free_reason, "state": b.state, "due_on": b.due_on.isoformat(),
            "paid_at": b.paid_at.isoformat() if b.paid_at else None}


class ChamaBillsView(APIView):
    """The chama's bills from JiraniChama, and this month so far. Every member sees them:
    what the chama pays us is the members' business too."""

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        today = timezone.localdate()
        period = f"{today.year}-{today.month:02d}"
        return Response({
            "this_month": services.compute(chama, period),
            "bills": [as_dict(b) for b in chama.bills.order_by("-period")[:24]],
            "pricing": {"price_usd": services.PRICE_USD,
                        "free_months": services.FREE_MONTHS,
                        "free_up_to_members": services.FREE_UP_TO_MEMBERS},
            "pay_to": services.pay_to(),
        })
