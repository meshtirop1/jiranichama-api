"""Year-end share-out. See docs/flows/shareout.md.

1. **Draft.** An official picks the cycle (from, to) and how to share: give savings back or
   keep them, how much of the earnings to keep as a reserve, and whether to share by
   savings or by savings times the months they were held. The figures are worked out from
   the ledger:
   - **earned** in the cycle: fines paid, loan interest, bank interest and investment
     income, less bank charges;
   - each member's **share** of what is shared out, in proportion to their weight;
   - their **unpaid fines** and **open loan** come off first;
   - what is left is **paid** to them.
2. **Vote.** The members vote on exactly these figures (their hash is on the ballot's
   question and in the result).
3. **Paying.** When it passes, the fines and loans are settled inside the books, and each
   member's payment is proposed in money out, approved by the officials as usual (no
   second members' vote: they have voted). A member paid nothing is settled in the books.
4. **Done** once every payment is matched in the bank. Savings given back come off the
   member's savings (``projections.position``), so nothing can be paid twice, at exit or
   in the next share-out.
"""

import hashlib
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.chamas.models import OFFICIAL_ROLES, Chama, Membership
from apps.contributions.projections import position
from apps.core import money
from apps.core.canonical import canonical_bytes
from apps.core.problems import Problem
from apps.fines.models import Fine
from apps.ledger import services as ledger
from apps.ledger.models import LedgerEntry
from apps.rules.models import version_at

from .models import ShareOut, ShareOutLine

DEFAULTS = {"return_savings": True, "keep_pct": 0, "weight": "savings_months"}
MAX_KEEP_PCT = 50
VOTE_DAYS = 7

# What the group earned (positive) or paid (bank charges, negative) in the cycle.
EARNING_TYPES = ("fine_paid", "loan_interest", "interest_earned", "investment_income",
                 "bank_charge")
SAVING_TYPES = ("contribution", "correction", "opening_balance")


def settings_from(rules: dict) -> dict:
    return {**DEFAULTS, **(rules.get("shareout") or {})}


def _day(e: LedgerEntry) -> date:
    from apps.documents.statements import _day as entry_day

    return entry_day(e)


def _months(a: date, b: date) -> int:
    return max(1, (b.year - a.year) * 12 + b.month - a.month + (1 if b.day >= a.day else 0))


def _reversed_ids(chama: Chama) -> set:
    return set(LedgerEntry.objects.filter(chama=chama, type="reversal")
               .values_list("reverses_id", flat=True))


def earned(chama: Chama, start: date, end: date) -> dict:
    gone = _reversed_ids(chama)
    by_type = {t: 0 for t in EARNING_TYPES}
    for e in LedgerEntry.objects.filter(chama=chama, type__in=EARNING_TYPES):
        if e.id in gone or not start <= _day(e) <= end:
            continue
        by_type[e.type] += e.amount_cents
    return {"by_type": by_type, "total_cents": max(sum(by_type.values()), 0)}


def _share_months(m: Membership, start: date, end: date, gone: set) -> int:
    """Money saved times the months it was held in the cycle."""
    total = 0
    for e in LedgerEntry.objects.filter(member=m, type__in=SAVING_TYPES):
        if e.id in gone or e.amount_cents <= 0:
            continue
        day = _day(e)
        if day > end:
            continue
        total += e.amount_cents * _months(max(day, start), end)
    return total


def compute(chama: Chama, *, start: date, end: date, return_savings: bool, keep_pct: int,
            weight: str) -> dict:
    from apps.loans import services as loans
    from apps.reconciliation.daily import summary

    if not 0 <= keep_pct <= MAX_KEEP_PCT:
        raise Problem("invalid", f"Keep between 0% and {MAX_KEEP_PCT}% as a reserve.")
    if weight not in ShareOut.Weight.values:
        raise Problem("invalid", "Share by savings, or by savings and months held.")
    today = timezone.localdate()
    members = list(Membership.objects.filter(
        chama=chama, state__in=[Membership.State.ACTIVE, Membership.State.PROBATION])
        .select_related("user").order_by("member_code"))
    gone = _reversed_ids(chama)
    pool = earned(chama, start, end)
    shared = pool["total_cents"] * (100 - keep_pct) // 100
    rows = []
    for m in members:
        p = position(m, today)
        w = (max(p.contributed_cents, 0) if weight == ShareOut.Weight.SAVINGS
             else _share_months(m, start, end, gone))
        loan = loans.running_loan(m)
        rows.append({"m": m, "savings": max(p.contributed_cents, 0), "weight": w,
                     "fines": p.fines_cents,
                     "loan": loans.balance(loan)["outstanding_cents"] if loan else 0})
    total_weight = sum(r["weight"] for r in rows)
    lines, handed = [], 0
    for r in rows:
        profit = shared * r["weight"] // total_weight if total_weight else 0
        handed += profit
        back = r["savings"] if return_savings else 0
        gross = back + profit
        fines = min(r["fines"], gross)
        loan = min(r["loan"], gross - fines)
        lines.append({"membership_id": str(r["m"].id), "member_code": r["m"].member_code,
                      "name": r["m"].user.display_name or r["m"].member_code,
                      "savings_cents": back, "weight": r["weight"], "profit_cents": profit,
                      "fines_cents": fines, "loan_cents": loan,
                      "payout_cents": gross - fines - loan})
    payout_total = sum(x["payout_cents"] for x in lines)
    bank = summary(chama)
    figures = {
        "from": start.isoformat(), "to": end.isoformat(),
        "return_savings": return_savings, "keep_pct": keep_pct, "weight": weight,
        "earned": pool, "shared_cents": handed,
        "reserve_cents": pool["total_cents"] - handed,
        "savings_back_cents": sum(x["savings_cents"] for x in lines),
        "fines_cents": sum(x["fines_cents"] for x in lines),
        "loans_cents": sum(x["loan_cents"] for x in lines),
        "payout_cents": payout_total,
        "bank_cents": bank["bank_cents"], "bank_status": bank["status"],
        "short_cents": (max(payout_total - bank["bank_cents"], 0)
                        if bank["bank_cents"] is not None else None),
        "lines": lines,
    }
    return figures


def _hash(figures: dict) -> str:
    return hashlib.sha256(canonical_bytes(figures)).hexdigest()


def _official(by: Membership) -> None:
    if by.role not in OFFICIAL_ROLES or by.state != Membership.State.ACTIVE:
        raise Problem("not_allowed", "An official prepares the share-out.",
                      status.HTTP_403_FORBIDDEN)


def draft(chama: Chama, by: Membership, *, period_from: date, period_to: date,
          return_savings: bool | None = None, keep_pct: int | None = None,
          weight: str | None = None, label: str = "") -> ShareOut:
    _official(by)
    if chama.state != Chama.State.ACTIVE:
        raise Problem("not_active", "The chama is not active.", status.HTTP_409_CONFLICT)
    if period_from >= period_to or period_to > timezone.localdate():
        raise Problem("invalid_period", "Choose a cycle that has ended, start before end.")
    if ShareOut.objects.filter(chama=chama, state__in=ShareOut.OPEN).exists():
        raise Problem("shareout_open", "Finish or cancel the share-out already open.",
                      status.HTTP_409_CONFLICT)
    version = version_at(chama, timezone.localdate())
    s = settings_from(version.rules if version else {})
    rs = s["return_savings"] if return_savings is None else return_savings
    kp = s["keep_pct"] if keep_pct is None else keep_pct
    wt = s["weight"] if weight is None else weight
    figures = compute(chama, start=period_from, end=period_to, return_savings=rs,
                      keep_pct=kp, weight=wt)
    return ShareOut.objects.create(
        chama=chama, label=(label or str(period_to.year))[:40], period_from=period_from,
        period_to=period_to, return_savings=rs, keep_pct=kp, weight=wt, figures=figures,
        figures_hash=_hash(figures), created_by=by)


def cancel(so: ShareOut, by: Membership) -> ShareOut:
    _official(by)
    if so.state != ShareOut.State.DRAFT:
        raise Problem("not_draft", "Only a draft can be cancelled.", status.HTTP_409_CONFLICT)
    so.state = ShareOut.State.CANCELLED
    so.save(update_fields=["state"])
    return so


def submit(so: ShareOut, by: Membership) -> ShareOut:
    """Put exactly these figures to the members."""
    from apps.votes import services as votes

    _official(by)
    if so.state != ShareOut.State.DRAFT:
        raise Problem("not_draft", "This share-out is not a draft.", status.HTTP_409_CONFLICT)
    f = so.figures
    if not f["lines"] or f["payout_cents"] + f["fines_cents"] + f["loans_cents"] <= 0:
        raise Problem("nothing_to_share", "There is nothing to share out.",
                      status.HTTP_409_CONFLICT)
    version = version_at(so.chama, timezone.localdate())
    total = money.fmt(f["payout_cents"], so.chama)
    with transaction.atomic():
        vote = votes.open_vote(
            so.chama, subject=f"shareout.shareout:{so.id}",
            question=(f"Share-out {so.label}: pay {total} to {len(f['lines'])} members as "
                      f"set out in JiraniChama (figures {so.figures_hash[:8]})?"),
            details=_details(so), rules=version.rules if version else {},
            closes_at=timezone.now() + timedelta(days=VOTE_DAYS), allow_change=True)
        so.state, so.vote = ShareOut.State.VOTING, vote
        so.save(update_fields=["state", "vote"])
    return so


def _details(so: ShareOut) -> str:
    f = so.figures
    k = lambda c: money.fmt(c, so.chama)  # noqa: E731
    lines = [f"Earned {f['from']} to {f['to']}: {k(f['earned']['total_cents'])}. "
             f"Shared: {k(f['shared_cents'])}; kept as reserve: {k(f['reserve_cents'])}.",
             f"Savings given back: {k(f['savings_back_cents'])}." if f["return_savings"]
             else "Savings stay in the chama."]
    for x in f["lines"]:
        lines.append(f"{x['member_code']} {x['name']}: {k(x['payout_cents'])}")
    return "\n".join(lines)


def on_vote_closed(vote) -> None:
    so = ShareOut.objects.select_for_update().get(pk=vote.subject.split(":", 1)[1])
    if so.state != ShareOut.State.VOTING:
        return
    if vote.state != "passed":
        so.state = ShareOut.State.REJECTED
        so.save(update_fields=["state"])
        return
    _approve(so)


def _approve(so: ShareOut) -> None:
    f = so.figures
    so.entry = ledger.append(
        chama=so.chama, type="shareout_approved", source="shareout", source_id=so.id,
        decision={"label": so.label, "from": f["from"], "to": f["to"],
                  "figures_hash": so.figures_hash, "payout_cents": f["payout_cents"],
                  "shared_cents": f["shared_cents"], "reserve_cents": f["reserve_cents"],
                  "savings_back_cents": f["savings_back_cents"],
                  "members": len(f["lines"]), "vote": str(so.vote_id)})
    so.state = ShareOut.State.PAYING
    so.save(update_fields=["state", "entry"])
    for x in f["lines"]:
        m = Membership.objects.get(pk=x["membership_id"])
        line = ShareOutLine.objects.create(
            shareout=so, membership=m, savings_cents=x["savings_cents"],
            profit_cents=x["profit_cents"], fines_cents=x["fines_cents"],
            loan_cents=x["loan_cents"], payout_cents=x["payout_cents"])
        _settle_inside(so, line)
        if line.payout_cents <= 0:
            # Nothing for the bank to pay: the savings given back are settled in the books.
            line.entry = ledger.append(
                chama=so.chama, type="shareout", source="shareout", source_id=so.id,
                member=m, decision={"shareout_id": str(so.id),
                                    "savings_out_cents": line.savings_cents,
                                    "paid_cents": 0})
            line.save(update_fields=["entry"])
    transaction.on_commit(lambda: propose_payments(so.pk))


def _settle_inside(so: ShareOut, line: ShareOutLine) -> None:
    """Unpaid fines and the open loan are paid from the member's share, in the books."""
    from apps.loans import services as loans

    m = line.membership
    version = version_at(so.chama, timezone.localdate())
    left = line.fines_cents
    for fine in Fine.objects.filter(membership=m, state=Fine.State.RAISED).order_by("period"):
        take = min(fine.outstanding_cents, left)
        if take <= 0:
            continue
        ledger.append(chama=so.chama, type="fine_paid", source="shareout", source_id=so.id,
                      member=m, amount_cents=take, period=fine.period,
                      rule_version_id=version.id, rule_refs=[fine.rule_ref],
                      decision={"fine_id": str(fine.id), "from": "share-out"})
        fine.paid_cents += take
        if fine.paid_cents >= fine.amount_cents:
            fine.state = Fine.State.PAID
        fine.save(update_fields=["paid_cents", "state"])
        left -= take
    loan = loans.running_loan(m)
    if loan is not None and line.loan_cents > 0:
        ledger.append(chama=so.chama, type="loan_repayment", source="shareout",
                      source_id=so.id, member=m, amount_cents=line.loan_cents,
                      rule_version_id=version.id,
                      decision={"loan_id": str(loan.id), "from": "share-out"})
        transaction.on_commit(lambda: loans.settle(loan.pk))


def propose_payments(shareout_id, by: Membership | None = None) -> int:
    """Each member's payment, in money out. Safe to run again: lines already proposed are
    skipped, so an official can retry after switching money out on."""
    from apps.payouts import services as payouts
    from apps.payouts.models import Proposal

    so = ShareOut.objects.select_related("chama", "created_by").get(pk=shareout_id)
    if so.state != ShareOut.State.PAYING:
        return 0
    n = 0
    for line in so.lines.filter(proposal__isnull=True, payout_cents__gt=0) \
            .select_related("membership__user"):
        # Neither the proposer nor the person paid approves. An official's own share is
        # proposed by that official, so the other officials can approve it; otherwise
        # three officials could never reach two approvals for one of them.
        m = line.membership
        proposer = (m if m.role in OFFICIAL_ROLES and m.state == Membership.State.ACTIVE
                    else by or so.created_by)
        try:
            with transaction.atomic():
                p = payouts.propose(so.chama, proposer, kind=Proposal.Kind.PAYOUT,
                                    amount_cents=line.payout_cents,
                                    purpose=f"Share-out {so.label}", payee=line.membership,
                                    allow_arrears=True, members_voted=True)
                line.proposal = p
                line.save(update_fields=["proposal"])
                n += 1
        except Problem:
            continue  # money out off, or a breach: an official retries later
    return n


def paid_extra(proposal) -> dict | None:
    """What the payout entry records for a share-out payment (payouts._write_payout)."""
    line = ShareOutLine.objects.filter(proposal=proposal).first()
    if line is None:
        return None
    return {"shareout_id": str(line.shareout_id), "savings_out_cents": line.savings_cents}


def on_paid(proposal) -> None:
    line = ShareOutLine.objects.filter(proposal=proposal).select_related("shareout").first()
    if line is None:
        return
    so = line.shareout
    left = so.lines.filter(payout_cents__gt=0).exclude(proposal__state="paid").count()
    if left == 0 and so.state == ShareOut.State.PAYING:
        so.state = ShareOut.State.DONE
        so.save(update_fields=["state"])


def as_dict(so: ShareOut, me: Membership | None = None) -> dict:
    lines = {str(x.membership_id): x for x in so.lines.select_related("proposal")}
    out = {"id": str(so.id), "label": so.label, "state": so.state,
           "from": so.period_from.isoformat(), "to": so.period_to.isoformat(),
           "return_savings": so.return_savings, "keep_pct": so.keep_pct, "weight": so.weight,
           "figures_hash": so.figures_hash, "vote_id": str(so.vote_id) if so.vote_id else None,
           "receipt": so.entry.receipt_code if so.entry_id else None,
           "created_at": so.created_at.isoformat(), **{k: v for k, v in so.figures.items()
                                                       if k != "lines"}}
    rows = []
    for x in so.figures["lines"]:
        line = lines.get(x["membership_id"])
        rows.append({**x, "proposal_state": line.proposal.state
                     if line and line.proposal_id else None,
                     "mine": bool(me) and x["membership_id"] == str(me.id)})
    out["lines"] = rows
    out["can_manage"] = bool(me) and me.role in OFFICIAL_ROLES
    return out
