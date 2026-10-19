"""Development only. Mounted by config/urls.py only when DEBUG is on and DEV_SMS_FILE
is set, so production can never serve it."""

import json

from django.conf import settings
from django.http import JsonResponse
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView


def dev_sms_outbox(request):
    to = request.GET.get("to", "")
    messages = []
    try:
        with open(settings.DEV_SMS_FILE, encoding="utf-8") as f:
            messages = [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        pass
    if to:
        messages = [m for m in messages if m["to"] == to]
    return JsonResponse({"messages": messages[-20:]})


class SmsInboundHook(APIView):
    """Messages members send to our shortcode (Africa's Talking incoming SMS callback:
    from, text, id). Checked by a secret path token and an optional IP allowlist. The
    answer goes back by SMS; the HTTP reply is always empty."""

    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []

    @extend_schema(exclude=True)
    def post(self, request, token):
        import hmac

        from .commands import handle

        expected = settings.SMS_INBOUND_TOKEN
        if not expected or not hmac.compare_digest(expected, token or ""):
            return Response(status=status.HTTP_404_NOT_FOUND)
        allowed = settings.SMS_INBOUND_ALLOWED_IPS
        if allowed and request.META.get("REMOTE_ADDR") not in allowed:
            return Response(status=status.HTTP_404_NOT_FOUND)
        data = request.data
        handle(str(data.get("from") or ""), str(data.get("text") or ""),
               str(data.get("id") or "")[:100])
        return Response(status=status.HTTP_200_OK)
