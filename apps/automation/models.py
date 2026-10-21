"""The chama runs itself (owner request 2026-10-21: "make this really super automatic").

``Notice`` is one message to one person: shown in the app's notices, and sent by SMS when
the person does not use the app or the matter is urgent. Its ``key`` makes every reminder
go once, however often the jobs run. ``MonthlyPack`` records that a chama's monthly pack
went out for a month, so the 1st-of-the-month job can run twice without sending twice.
"""

from django.conf import settings
from django.db import models

from apps.chamas.models import Chama


class Notice(models.Model):
    """One message to one person. Insert only, apart from when it was read and whether
    the SMS went."""

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name="notices")
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, null=True, blank=True,
                              related_name="notices")
    kind = models.CharField(max_length=24)  # what it is about, for the app's icon
    key = models.CharField(max_length=120)  # "due-soon:2026-11:<membership>"
    text = models.CharField(max_length=480)  # in the person's language
    data = models.JSONField(default=dict, blank=True)  # where the app opens it
    urgent = models.BooleanField(default=False)
    sms_sent = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE = ["sms_sent", "read_at"]

    class Meta:
        db_table = "automation_notice"
        constraints = [models.UniqueConstraint(fields=["user", "key"],
                                               name="one_notice_per_key")]
        indexes = [models.Index(fields=["user", "-created_at"])]


class MonthlyPack(models.Model):
    """The monthly pack went out for this chama and month. Insert only."""

    id = models.BigAutoField(primary_key=True)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="+")
    period = models.CharField(max_length=7)
    members = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "automation_monthly_pack"
        constraints = [models.UniqueConstraint(fields=["chama", "period"],
                                               name="one_pack_per_month")]
