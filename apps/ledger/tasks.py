import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.chamas.models import Chama

from . import services
from .models import LedgerEntry

log = logging.getLogger(__name__)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=8)
def sign_entry(entry_id: str) -> None:
    services.sign_entry(entry_id)


@shared_task
def sign_pending(limit: int = 1000) -> int:
    """Sweep for entries whose signing event was missed (worker down, KMS error)."""
    pending = LedgerEntry.objects.filter(platform_signature__isnull=True).order_by("at")
    count = 0
    for entry_id in pending.values_list("id", flat=True)[:limit]:
        services.sign_entry(entry_id)
        count += 1
    return count


@shared_task
def verify_chains() -> dict:
    """Nightly: recompute every chain. A break is an integrity alert (Phase 2 alerts
    members and freezes proposals; Phase 0 logs it for staff)."""
    broken, stale_unsigned = [], []
    cutoff = timezone.now() - timedelta(minutes=10)
    for chama in Chama.objects.all().iterator():
        report = services.verify_chain(chama)
        if not report.ok:
            broken.append(report.chama_id)
            log.error("ledger chain broken for chama %s: %s", report.chama_id, report.problems)
        if report.unsigned and LedgerEntry.objects.filter(
            chama=chama, seq__in=report.unsigned, at__lt=cutoff
        ).exists():
            stale_unsigned.append(report.chama_id)
            log.error("unsigned ledger entries older than 10 minutes in chama %s",
                      report.chama_id)
    return {"broken": broken, "stale_unsigned": stale_unsigned}
