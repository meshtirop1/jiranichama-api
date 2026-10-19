"""Investments (4.10, Phase 5). See docs/flows/investments.md.

An official records what the chama owns: bought through a paid money-out proposal (so
the cost is the money that left the bank), or already owned before the chama used the
app. Valuations are recorded with their source and count once a second official
confirms them. Income (dividends, interest, rent) and sale proceeds are bank credits the
treasurer assigns to the investment, written as investment_income entries, so the
reconciliation counts them like any money.
"""

import hashlib

from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.chamas.models import OFFICIAL_ROLES, Chama, Membership
from apps.core.problems import Problem
from apps.feeds.models import BankTransaction
from apps.ledger import services as ledger

from .models import Investment, Valuation

INCOME_KINDS = ("dividend", "interest", "rent", "sale", "other")
INCOME_KIND_CHOICES = [(k, k) for k in INCOME_KINDS]


def _official(m: Membership) -> None:
    if m.role not in OFFICIAL_ROLES:
        raise Problem("not_allowed", "An official records investments.",
                      status.HTTP_403_FORBIDDEN)


def record(chama: Chama, by: Membership, *, kind: str, name: str, details: str = "",
           bought_on, cost_cents: int, purchase_id=None) -> Investment:
    from apps.payouts.models import Proposal

    _official(by)
    if kind not in Investment.Kind.values or not (name or "").strip():
        raise Problem("invalid", "Say what it is and give it a name.")
    purchase = None
    if purchase_id:
        purchase = Proposal.objects.filter(chama=chama, pk=purchase_id).first()
        if purchase is None or purchase.state != Proposal.State.PAID:
            raise Problem("purchase_not_paid", "Link a money-out payment that has been paid.",
                          status.HTTP_409_CONFLICT)
        if Investment.objects.filter(purchase=purchase).exists():
            raise Problem("already_recorded", "That payment is already an investment.",
                          status.HTTP_409_CONFLICT)
        cost_cents = purchase.amount_cents
    if cost_cents is None or cost_cents < 0:
        raise Problem("invalid_amount", "Enter what it cost.")
    with transaction.atomic():
        entry = ledger.append(
            chama=chama, type="investment_recorded", source="investment", member=by,
            decision={"kind": kind, "name": name.strip()[:120], "cost_cents": cost_cents,
                      "bought_on": str(bought_on),
                      "purchase": str(purchase.id) if purchase else None,
                      "receipt_of_payment": purchase.entry.receipt_code
                      if purchase and purchase.entry_id else None})
        return Investment.objects.create(
            chama=chama, kind=kind, name=name.strip()[:120], details=(details or "").strip(),
            bought_on=bought_on, cost_cents=cost_cents, purchase=purchase, recorded_by=by,
            entry=entry)


def value(inv: Investment, by: Membership, *, value_cents: int, as_of, source: str,
          document=None) -> Valuation:
    _official(by)
    if value_cents is None or value_cents < 0 or not (source or "").strip():
        raise Problem("invalid", "Give the value and where it comes from.")
    if as_of > timezone.localdate():
        raise Problem("bad_dates", "A valuation is for a day that has passed.")
    v = Valuation(investment=inv, value_cents=value_cents, as_of=as_of,
                  source=source.strip()[:200], recorded_by=by)
    if document is not None:
        digest = hashlib.sha256()
        for chunk in document.chunks():
            digest.update(chunk)
        document.seek(0)
        v.document_sha256 = digest.hexdigest()
        v.document.save(f"{inv.id}-{as_of}.pdf", document, save=False)
    v.save()
    return v


def confirm(v: Valuation, by: Membership) -> Valuation:
    _official(by)
    if by.id == v.recorded_by_id:
        raise Problem("second_official_required", "A different official confirms it.",
                      status.HTTP_403_FORBIDDEN)
    with transaction.atomic():
        v = Valuation.objects.select_for_update().get(pk=v.pk)
        if v.confirmed_at:
            raise Problem("already_answered", "Already confirmed.", status.HTTP_409_CONFLICT)
        inv = v.investment
        v.entry = ledger.append(
            chama=inv.chama, type="valuation", source="investment", source_id=inv.id,
            decision={"investment": str(inv.id), "name": inv.name,
                      "value_cents": v.value_cents, "as_of": str(v.as_of),
                      "source": v.source, "document_sha256": v.document_sha256 or None,
                      "recorded_by": v.recorded_by.member_code,
                      "confirmed_by": by.member_code})
        v.confirmed_by, v.confirmed_at = by, timezone.now()
        v.save(update_fields=["entry", "confirmed_by", "confirmed_at"])
    return v


def add_income(inv: Investment, by: Membership, *, txn_id, kind: str,
               note: str = "") -> Investment:
    """An unmatched bank credit was income from this investment, or its sale."""
    from apps.rules.models import version_at

    if by.role != Membership.Role.TREASURER:
        raise Problem("not_allowed", "The treasurer assigns bank credits.",
                      status.HTTP_403_FORBIDDEN)
    if kind not in INCOME_KINDS:
        raise Problem("invalid", "Choose what kind of income it is.")
    if inv.state != Investment.State.HELD:
        raise Problem("investment_sold", "This investment has been sold.",
                      status.HTTP_409_CONFLICT)
    with transaction.atomic():
        txn = BankTransaction.objects.select_for_update().filter(
            chama=inv.chama, pk=txn_id, direction=BankTransaction.Direction.CREDIT,
            state=BankTransaction.State.UNMATCHED).first()
        if txn is None:
            raise Problem("not_open", "Choose an unmatched bank credit.",
                          status.HTTP_409_CONFLICT)
        version = version_at(inv.chama, txn.value_date)
        ledger.append(chama=inv.chama, type="investment_income", source="bank_txn",
                      source_id=txn.id, amount_cents=txn.amount_cents,
                      rule_version_id=version.id if version else None,
                      decision={"investment": str(inv.id), "name": inv.name, "kind": kind,
                                "note": (note or "").strip()[:300],
                                "bank_ref": txn.external_ref,
                                "value_date": txn.value_date.isoformat(),
                                "assigned_by": by.member_code})
        txn.state = BankTransaction.State.RECORDED
        txn.save(update_fields=["state"])
        if kind == "sale":
            inv.state, inv.sold_on = Investment.State.SOLD, txn.value_date
            inv.save(update_fields=["state", "sold_on"])
    return inv


def latest_value(inv: Investment) -> Valuation | None:
    return inv.valuations.filter(confirmed_at__isnull=False).order_by("-as_of",
                                                                      "-created_at").first()


def income_cents(inv: Investment) -> int:
    from django.db.models import Sum

    from apps.ledger.models import LedgerEntry

    return (LedgerEntry.objects.filter(chama_id=inv.chama_id, type="investment_income",
                                       decision__investment=str(inv.id))
            .aggregate(t=Sum("amount_cents"))["t"] or 0)


def portfolio(chama: Chama) -> dict:
    """What the chama owns: each holding at cost and at its latest confirmed value, plus
    the bank balance from the last reconciliation."""
    from apps.reconciliation.models import ReconciliationRun

    held = Investment.objects.filter(chama=chama, state=Investment.State.HELD)
    cost = sum(i.cost_cents for i in held)
    worth = 0
    for i in held:
        v = latest_value(i)
        worth += v.value_cents if v else i.cost_cents
    run = ReconciliationRun.objects.filter(chama=chama).order_by("-at").first()
    bank = run.bank_cents if run else None
    return {"held": held.count(), "cost_cents": cost, "value_cents": worth,
            "bank_cents": bank,
            "net_worth_cents": (bank or 0) + worth if bank is not None else None}
