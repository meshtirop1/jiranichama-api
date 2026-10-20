import uuid

from django.conf import settings
from django.db import models

from apps.chamas.models import Chama


class RegistrationProgress(models.Model):
    """Where a chama is with registering at the government office: which papers and steps
    the officials have ticked off, and the certificate once they have it. One row per
    chama, changed as they go (it is a to-do list, not a record of money)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.OneToOneField(Chama, on_delete=models.PROTECT,
                                 related_name="registration_progress")
    route = models.CharField(max_length=40, default="community_group")
    ticked = models.JSONField(default=list, blank=True)  # paper ids and "step:N"
    certificate_no = models.CharField(max_length=60, blank=True, default="")
    registered_on = models.DateField(null=True, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "registration_progress"

    @property
    def registered(self) -> bool:
        return bool(self.certificate_no)
