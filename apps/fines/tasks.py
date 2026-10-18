from celery import shared_task
from django.utils import timezone

from apps.chamas.models import Chama

from .services import raise_late_fines


@shared_task
def raise_due_fines() -> int:
    today = timezone.localdate()
    return sum(raise_late_fines(c, today)
               for c in Chama.objects.filter(state=Chama.State.ACTIVE).iterator())
