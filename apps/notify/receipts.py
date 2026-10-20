"""Payment receipts (5.3 step 8).

When a payment is recorded for a member, members who use the app see it there; members
who have not opened the app in ``RECEIPT_SMS_IF_AWAY_DAYS`` days (or never) get one SMS
per payment:

    JiraniChama: KES 1,000 received for Mar from Wanjiru (JC-4821-017). Umoja Women
    Group. Ref 7KQ2-M9. Check: VERIFY 7KQ2M9

The reference is the first entry's receipt code; VERIFY answers by SMS (sms-commands).
"""

from datetime import date, timedelta

from django.conf import settings
from django.utils import timezone

from apps.chamas.models import Membership
from apps.core import money
from apps.ledger.models import LedgerEntry

from .sms import SmsFailed, send_sms


def _kes(cents: int, of=None) -> str:
    return money.fmt(cents, of)


def _month(period: str | None, sw: bool) -> str:
    if not period:
        return ""
    d = date.fromisoformat(period + "-01")
    if sw:
        names = ["Jan", "Feb", "Mac", "Apr", "Mei", "Jun", "Jul", "Ago", "Sep", "Okt",
                 "Nov", "Des"]
        return names[d.month - 1]
    return d.strftime("%b")


def wants_sms(membership: Membership) -> bool:
    last = membership.user.last_app_at
    away = timedelta(days=settings.RECEIPT_SMS_IF_AWAY_DAYS)
    return last is None or timezone.now() - last > away


def send_payment_receipt(membership_id, entry_ids: list) -> bool:
    m = Membership.objects.select_related("user", "chama").get(pk=membership_id)
    if not wants_sms(m):
        return False
    entries = list(LedgerEntry.objects.filter(pk__in=entry_ids).order_by("seq"))
    if not entries:
        return False
    total = sum(e.amount_cents for e in entries if e.type in ("contribution", "fine_paid"))
    if total <= 0:
        return False
    sw = m.user.language == "sw"
    months = sorted({e.period for e in entries if e.type == "contribution" and e.period})
    what = ", ".join(_month(p, sw) for p in months)
    code = entries[0].receipt_code
    shown = f"{code[:4]}-{code[4:]}"
    name = m.user.display_name or m.member_code
    if sw:
        text = (f"JiraniChama: {_kes(total, m)} zimepokelewa"
                + (f" za {what}" if what else "") + f" kutoka kwa {name} ({m.member_code}). "
                f"{m.chama.name}. Kumb {shown}. Hakiki: HAKIKI {code}")
    else:
        text = (f"JiraniChama: {_kes(total, m)} received" + (f" for {what}" if what else "")
                + f" from {name} ({m.member_code}). {m.chama.name}. Ref {shown}. "
                f"Check: VERIFY {code}")
    try:
        send_sms(m.user.phone, text)
    except SmsFailed:
        return False
    return True
