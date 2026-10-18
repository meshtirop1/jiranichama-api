"""Development only. Mounted by config/urls.py only when DEBUG is on and DEV_SMS_FILE
is set, so production can never serve it."""

import json

from django.conf import settings
from django.http import JsonResponse


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
