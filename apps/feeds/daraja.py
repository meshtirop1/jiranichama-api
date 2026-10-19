"""M-Pesa paybill and till notifications (Daraja C2B, 6.9).

For a chama with its own paybill or till, Safaricom calls us within seconds of each
payment. Validation accepts everything (we never refuse a member's payment). The
confirmation is stored once per TransID and matched straight away: the member code in
BillRefNumber, then the payer's phone (hashed on arrival). When the same payment later
appears in a bank statement, that line is recorded as a duplicate of this one, never
counted twice. See docs/flows/paybill.md.
"""

import hmac
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.phones import InvalidPhone, normalise_phone, phone_hash
from apps.chamas.services import active_bank_account

from .models import BankTransaction, DarajaChannel

ACCEPT = {"ResultCode": 0, "ResultDesc": "Accepted"}


def channel_for(token: str) -> DarajaChannel | None:
    for ch in DarajaChannel.objects.filter(active=True).select_related("chama"):
        if hmac.compare_digest(ch.token, token or ""):
            return ch
    return None


def _cents(raw) -> int:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise ValueError(f"not an amount: {raw!r}") from None
    return int((value * 100).to_integral_value())


def _value_date(raw: str):
    try:
        return timezone.make_aware(datetime.strptime(str(raw), "%Y%m%d%H%M%S")).date()
    except ValueError:
        return timezone.localdate()


def _phone_hash(raw: str) -> str:
    """Daraja sends the payer's number, or (newer integrations) a masked or hashed one.
    Only a real number is hashed with our key; anything else is ignored."""
    if not raw or not re.fullmatch(r"\+?\d{9,13}", str(raw)):
        return ""
    try:
        return phone_hash(normalise_phone(str(raw)))
    except InvalidPhone:
        return ""


def receive(channel: DarajaChannel, payload: dict) -> BankTransaction | None:
    """Store one confirmation and match it. Safe to receive twice (TransID)."""
    from apps.reconciliation.services import match_credit

    trans_id = str(payload.get("TransID") or "").strip()[:100]
    if not trans_id:
        return None
    account = active_bank_account(channel.chama)
    if account is None:
        return None
    try:
        amount = _cents(payload.get("TransAmount"))
    except ValueError:
        return None
    ref = str(payload.get("BillRefNumber") or "").strip()
    payer = " ".join(str(payload.get(k) or "").strip()
                     for k in ("FirstName", "MiddleName", "LastName")).strip()
    txn = BankTransaction(
        chama=channel.chama, bank_account=account, source=BankTransaction.Source.DARAJA,
        external_ref=trans_id, direction=BankTransaction.Direction.CREDIT,
        amount_cents=amount, value_date=_value_date(payload.get("TransTime")),
        payer_phone_hash=_phone_hash(payload.get("MSISDN")),
        state=BankTransaction.State.UNMATCHED)
    txn.set_narration(f"M-PESA {trans_id} {ref} {payer}".strip())
    try:
        with transaction.atomic():
            txn.save()
    except IntegrityError:
        return None  # already have this TransID
    match_credit(txn)
    return txn


# --- Statement lines that are the same payment ------------------------------------------------

_MPESA_REF = re.compile(r"\b([A-Z0-9]{10})\b")


def daraja_twin(*, bank_account, amount_cents: int, value_date, narration: str,
                reference: str) -> BankTransaction | None:
    """The M-Pesa notification a statement credit repeats, if any: by the M-Pesa reference
    in the line, otherwise by amount, day (one either way) and the member code."""
    qs = BankTransaction.objects.filter(
        bank_account=bank_account, source=BankTransaction.Source.DARAJA,
        direction=BankTransaction.Direction.CREDIT, amount_cents=amount_cents)
    covered = BankTransaction.objects.filter(duplicate_of__isnull=False) \
        .values_list("duplicate_of_id", flat=True)
    qs = qs.exclude(pk__in=covered)
    text = f"{reference} {narration}".upper()
    for ref in _MPESA_REF.findall(text):
        found = qs.filter(external_ref=ref).first()
        if found:
            return found
    near = list(qs.filter(value_date__gte=value_date - timedelta(days=1),
                          value_date__lte=value_date + timedelta(days=1)))
    from apps.reconciliation.services import _MEMBER_CODE

    codes = {f"JC-{a}-{b}" for a, b in _MEMBER_CODE.findall(text)}
    if codes:
        near = [t for t in near if any(c in t.narration.upper() for c in codes)]
    return near[0] if len(near) == 1 else None


# --- Registering our URLs with Safaricom ---------------------------------------------------

def _token() -> str:
    r = requests.get(f"{settings.DARAJA_BASE_URL}/oauth/v1/generate?grant_type=client_credentials",
                     auth=(settings.DARAJA_CONSUMER_KEY, settings.DARAJA_CONSUMER_SECRET),
                     timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def hook_urls(channel: DarajaChannel) -> dict:
    base = settings.DARAJA_CALLBACK_BASE_URL.rstrip("/")
    return {"ValidationURL": f"{base}/api/v1/hooks/daraja/{channel.token}/validation/",
            "ConfirmationURL": f"{base}/api/v1/hooks/daraja/{channel.token}/confirmation/"}


def register_urls(channel: DarajaChannel) -> dict:
    """Ask Safaricom to send this shortcode's payments to us (C2B v2 register URL)."""
    body = {"ShortCode": channel.shortcode, "ResponseType": "Completed", **hook_urls(channel)}
    r = requests.post(f"{settings.DARAJA_BASE_URL}/mpesa/c2b/v2/registerurl", json=body,
                      headers={"Authorization": f"Bearer {_token()}"}, timeout=15)
    r.raise_for_status()
    channel.registered_at = timezone.now()
    channel.save(update_fields=["registered_at"])
    return r.json()
