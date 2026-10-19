"""Inbound SMS (4.9): commands from members on basic phones, and their votes."""

from django.conf import settings
from django.db import models


class InboundSms(models.Model):
    """One message a member sent us. The message text is not stored (it may hold a code);
    only who sent it (a keyed hash of the number), the command word and whether we
    answered. Insert only. Also the basis of the per-phone limit."""

    id = models.BigAutoField(primary_key=True)
    phone_hash = models.CharField(max_length=80, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                             blank=True, related_name="+")
    command = models.CharField(max_length=16)
    provider_id = models.CharField(max_length=100, blank=True, default="")
    replied = models.BooleanField(default=False)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notify_inbound_sms"
