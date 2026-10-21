"""Pay now: the M-Pesa PIN prompt on the member's phone (Lipa na M-Pesa Online, 2026-10-21).

Only for a chama with its **own** paybill or till whose passkey staff have entered: the
money goes from the member's M-Pesa straight to the chama's shortcode. JiraniChama only
asks Safaricom to show the prompt; it never holds the money. A bank's paybill (Equity
247247 and the like) cannot be prompted: only the shortcode's owner can register for it.

When Safaricom reports the prompt paid, the payment is stored exactly as a C2B
confirmation would be (``daraja.receive``), with the member code of the member who asked,
so it is matched, judged by the rules and receipted like every other payment, and never
counted twice when the same receipt also arrives by C2B or on a statement.
"""

import base64
import math
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone
from rest_framework import status

from apps.core.problems import Problem

from . import daraja
from .models import DarajaChannel, StkRequest

MIN_CENTS = 100  # KES 1
MAX_CENTS = 150_000 * 100  # M-Pesa's limit for one payment
WAIT = timedelta(minutes=2)  # one prompt at a time per member


def channel_for_chama(chama) -> DarajaChannel | None:
    if chama.currency != "KES":
        return None  # M-Pesa Kenya only, for now
    return (DarajaChannel.objects.filter(chama=chama, active=True)
            .exclude(stk_passkey_enc=None).order_by("created_at").first())


def callback_url(channel: DarajaChannel) -> str:
    base = settings.DARAJA_CALLBACK_BASE_URL.rstrip("/")
    return f"{base}/api/v1/hooks/daraja/{channel.token}/stk/"


def _password(channel: DarajaChannel, stamp: str) -> str:
    raw = f"{channel.shortcode}{channel.stk_passkey()}{stamp}".encode()
    return base64.b64encode(raw).decode()


def start(member, amount_cents: int) -> StkRequest:
    chama = member.chama
    channel = channel_for_chama(chama)
    if channel is None:
        raise Problem("pay_now_unavailable", "This chama's paybill is not set up for Pay now. "
                                             "Pay the usual way.", status.HTTP_409_CONFLICT)
    if not MIN_CENTS <= amount_cents <= MAX_CENTS:
        raise Problem("invalid_amount", "Enter an amount M-Pesa allows (KES 1 to 150,000).")
    if StkRequest.objects.filter(membership=member, state=StkRequest.State.SENT,
                                 created_at__gt=timezone.now() - WAIT).exists():
        raise Problem("prompt_pending", "A prompt is already on your phone. Enter your M-Pesa "
                                        "PIN there, or wait two minutes.",
                      status.HTTP_429_TOO_MANY_REQUESTS)
    shillings = math.ceil(amount_cents / 100)  # M-Pesa takes whole shillings
    phone = member.user.phone.lstrip("+")
    req = StkRequest.objects.create(chama=chama, channel=channel, membership=member,
                                    amount_cents=shillings * 100,
                                    phone_hint=member.user.phone_hint)
    stamp = timezone.localtime().strftime("%Y%m%d%H%M%S")
    body = {
        "BusinessShortCode": channel.shortcode, "Password": _password(channel, stamp),
        "Timestamp": stamp,
        "TransactionType": ("CustomerBuyGoodsOnline" if channel.kind == DarajaChannel.Kind.TILL
                            else "CustomerPayBillOnline"),
        "Amount": shillings, "PartyA": phone, "PartyB": channel.shortcode,
        "PhoneNumber": phone, "CallBackURL": callback_url(channel),
        "AccountReference": member.member_code[:12], "TransactionDesc": "Chama",
    }
    try:
        r = requests.post(f"{settings.DARAJA_BASE_URL}/mpesa/stkpush/v1/processrequest",
                          json=body, headers={"Authorization": f"Bearer {daraja._token()}"},
                          timeout=20)
        data = r.json() if r.content else {}
    except (requests.RequestException, ValueError) as exc:
        req.state, req.result_text = StkRequest.State.REFUSED, str(exc)[:200]
        req.save(update_fields=["state", "result_text"])
        raise Problem("mpesa_unreachable", "M-Pesa did not answer. Try again in a minute.",
                      status.HTTP_503_SERVICE_UNAVAILABLE) from None
    if str(data.get("ResponseCode")) != "0":
        req.state = StkRequest.State.REFUSED
        req.result_code = str(data.get("errorCode") or data.get("ResponseCode") or "")[:12]
        req.result_text = str(data.get("errorMessage") or data.get("ResponseDescription")
                              or "")[:200]
        req.save(update_fields=["state", "result_code", "result_text"])
        raise Problem("mpesa_refused", "M-Pesa could not send the prompt. Pay the usual way.",
                      status.HTTP_409_CONFLICT)
    req.checkout_request_id = str(data.get("CheckoutRequestID") or "")[:80]
    req.save(update_fields=["checkout_request_id"])
    return req


def _items(callback: dict) -> dict:
    meta = (callback.get("CallbackMetadata") or {}).get("Item") or []
    return {i.get("Name"): i.get("Value") for i in meta if isinstance(i, dict)}


def receive_callback(channel: DarajaChannel, payload: dict) -> StkRequest | None:
    """Safaricom's answer to a prompt. Safe to receive twice."""
    cb = ((payload or {}).get("Body") or {}).get("stkCallback") or {}
    checkout = str(cb.get("CheckoutRequestID") or "")
    req = (StkRequest.objects.filter(channel=channel, checkout_request_id=checkout)
           .select_related("membership", "chama").first() if checkout else None)
    if req is None or req.state != StkRequest.State.SENT:
        return req
    req.result_code = str(cb.get("ResultCode"))[:12]
    req.result_text = str(cb.get("ResultDesc") or "")[:200]
    if req.result_code != "0":
        req.state = StkRequest.State.FAILED
        req.save(update_fields=["state", "result_code", "result_text"])
        return req
    items = _items(cb)
    receipt = str(items.get("MpesaReceiptNumber") or "")
    # Stored as the C2B confirmation it is, with the member code of the member who asked.
    txn = daraja.receive(channel, {
        "TransID": receipt, "BusinessShortCode": channel.shortcode,
        "TransAmount": items.get("Amount"), "BillRefNumber": req.membership.member_code,
        "MSISDN": str(items.get("PhoneNumber") or ""),
        "TransTime": str(items.get("TransactionDate") or ""), "FirstName": "PAY NOW"})
    if txn is None:  # already stored (the C2B copy came first) or not M-Pesa-shaped
        from .models import BankTransaction

        txn = BankTransaction.objects.filter(chama=req.chama, source="daraja",
                                             external_ref=receipt).first()
    req.state, req.receipt, req.bank_txn = StkRequest.State.PAID, receipt[:20], txn
    req.save(update_fields=["state", "result_code", "result_text", "receipt", "bank_txn"])
    return req
