"""The monthly pack, on the 1st (owner request 2026-10-21).

At 05:00 on the 1st, after the bills (03:00), for every active chama and last month:
- **every current member** gets their sealed statement PDF for the month (in their own
  language) and a short summary: paid in the month, savings, what they owe;
- **the officials** get the chama's report PDF for the month and the key numbers: share of
  contributions paid, members behind, the bank check;
- **the treasurer** is told about JiraniChama's bill, with one tap to propose paying it.

``MonthlyPack`` marks the month done, so the job can run again without sending twice.
Each statement is sealed into the chain like any statement (documents.services.issue), so
a member can take it to a bank or lender and anyone can check it.
"""

from datetime import date

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.billing import services as billing
from apps.billing.models import Bill
from apps.chamas.models import OFFICIAL_ROLES, Chama, Membership
from apps.contributions.projections import position
from apps.core import money
from apps.documents import services as documents
from apps.documents.models import IssuedDocument

from .models import MonthlyPack
from .notices import lang, notify


def _month_name(period: str, sw: bool) -> str:
    year, month = (int(x) for x in period.split("-"))
    d = date(year, month, 1)
    if sw:
        names = ["Januari", "Februari", "Machi", "Aprili", "Mei", "Juni", "Julai", "Agosti",
                 "Septemba", "Oktoba", "Novemba", "Desemba"]
        return f"{names[month - 1]} {year}"
    return d.strftime("%B %Y")


def _member_pack(chama: Chama, m: Membership, period: str, start, end) -> bool:
    language = lang(m.user)
    doc = documents.issue(kind=IssuedDocument.Kind.MEMBER_STATEMENT, chama=chama,
                          by_user=m.user, subject=m, start=start, end=end, language=language)
    paid = doc.data["totals"]["contributed_cents"]
    p = position(m, timezone.localdate())
    owe = p.arrears_cents + p.fines_cents
    f = lambda c: money.fmt(c, chama)  # noqa: E731
    text = {
        "en": f"{_month_name(period, False)}: you paid {f(paid)}. Savings {f(p.contributed_cents)}."
              + (f" You owe {f(owe)}." if owe else " You owe nothing.")
              + " Your statement is ready in JiraniChama.",
        "sw": f"{_month_name(period, True)}: ulilipa {f(paid)}. Akiba {f(p.contributed_cents)}."
              + (f" Unadaiwa {f(owe)}." if owe else " Hudaiwi chochote.")
              + " Taarifa yako iko tayari kwenye JiraniChama.",
    }
    return notify(m.user, chama=chama, kind="monthly", key=f"monthly:{period}:{m.id}",
                  text=text, sms="away",
                  data={"screen": "document", "id": str(doc.id)}) is not None


def _officials_pack(chama: Chama, period: str, start, end, officials: list) -> int:
    from apps.reconciliation.daily import summary
    from apps.reports.analytics import report

    doc = documents.issue(kind=IssuedDocument.Kind.CHAMA_STATEMENT, chama=chama,
                          by_user=officials[0].user, subject=None, start=start, end=end,
                          language="en")
    figures = report(chama, months=1, today=end)
    row = figures["months"][-1] if figures["months"] else {}
    pct = row.get("paid_pct")
    bank = summary(chama)["status"]
    bank_en = {"match": "the records match the bank", "open": "there is a gap with the bank",
               "breach": "the bank check is in breach",
               "no_statement": "no bank statement yet"}[bank]
    bank_sw = {"match": "kumbukumbu zinalingana na benki",
               "open": "kuna tofauti na benki", "breach": "ukaguzi wa benki una tatizo kubwa",
               "no_statement": "bado hakuna taarifa ya benki"}[bank]
    f = lambda c: money.fmt(c, chama)  # noqa: E731
    text = {
        "en": f"{_month_name(period, False)} report: {f(row.get('paid_cents', 0))} paid of "
              f"{f(row.get('due_cents', 0))} due" + (f" ({pct}%)" if pct is not None else "")
              + f". {figures['behind_count']} members behind; {bank_en}. The chama report "
              "is ready.",
        "sw": f"Ripoti ya {_month_name(period, True)}: {f(row.get('paid_cents', 0))} "
              f"zimelipwa kati ya {f(row.get('due_cents', 0))}"
              + (f" ({pct}%)" if pct is not None else "")
              + f". Wanachama {figures['behind_count']} wako nyuma; {bank_sw}. Ripoti ya "
              "chama iko tayari.",
    }
    n = 0
    for o in officials:
        if notify(o.user, chama=chama, kind="monthly", key=f"monthly-report:{period}",
                  text=text, sms="away", data={"screen": "document", "id": str(doc.id)}):
            n += 1
    return n


def _bill_notice(chama: Chama, period: str, officials: list) -> int:
    bill = Bill.objects.filter(chama=chama, period=period, state=Bill.State.OPEN).first()
    to = billing.pay_to()
    if bill is None or not to["paybill"]:
        return 0
    f = money.fmt(bill.total_cents, chama)
    account = f"{to['account_prefix']}{chama.member_code_prefix.replace('JC-', '')}"
    text = {"en": f"JiraniChama's bill for {_month_name(period, False)}: {f}, due "
                  f"{bill.due_on:%d %b}. Paybill {to['paybill']}, account {account}. Tap to "
                  "propose the payment.",
            "sw": f"Bili ya JiraniChama ya {_month_name(period, True)}: {f}, mwisho "
                  f"{bill.due_on:%d %b}. Paybill {to['paybill']}, akaunti {account}. Gusa "
                  "kupendekeza malipo."}
    n = 0
    for o in officials:
        if o.role != Membership.Role.TREASURER:
            continue
        if notify(o.user, chama=chama, kind="bill", key=f"bill:{bill.id}", text=text,
                  sms="away", data={"screen": "bill", "id": str(bill.id)}):
            n += 1
    return n


def pack(chama: Chama, period: str) -> MonthlyPack | None:
    """Send one chama's pack for ``period``, unless it went already."""
    if MonthlyPack.objects.filter(chama=chama, period=period).exists():
        return None
    start, end = billing.month_bounds(period)
    members = list(Membership.objects.filter(
        chama=chama, state__in=[Membership.State.ACTIVE, Membership.State.PROBATION],
        joined_on__isnull=False, joined_on__lte=end).select_related("user"))
    sent = 0
    for m in members:
        if _member_pack(chama, m, period, start, end):
            sent += 1
    officials = [m for m in members if m.role in OFFICIAL_ROLES]
    if officials:
        _officials_pack(chama, period, start, end, officials)
        _bill_notice(chama, period, officials)
    try:
        with transaction.atomic():
            return MonthlyPack.objects.create(chama=chama, period=period, members=sent)
    except IntegrityError:
        return None


def pack_all(period: str | None = None) -> int:
    period = period or billing.previous_period()
    n = 0
    for chama in Chama.objects.filter(state=Chama.State.ACTIVE):
        if pack(chama, period):
            n += 1
    return n


def propose_bill(bill: Bill, by: Membership):
    """One tap from the bill notice: the treasurer proposes paying JiraniChama's bill as
    money out, approved by the other officials like any payment."""
    from rest_framework import status

    from apps.core.problems import Problem
    from apps.payouts import services as payouts

    to = billing.pay_to()
    if bill.chama_id != by.chama_id or bill.state != Bill.State.OPEN:
        raise Problem("not_found", "That bill is not open.", status.HTTP_404_NOT_FOUND)
    if not to["paybill"]:
        raise Problem("no_paybill", "JiraniChama's paybill is not set yet. Nothing is due.",
                      status.HTTP_409_CONFLICT)
    account = f"{to['account_prefix']}{bill.chama.member_code_prefix.replace('JC-', '')}"
    return payouts.propose(bill.chama, by, kind="withdrawal", amount_cents=bill.total_cents,
                           payee_name="JiraniChama",
                           payee_account=f"Paybill {to['paybill']} account {account}",
                           purpose=f"JiraniChama bill {bill.period}")
