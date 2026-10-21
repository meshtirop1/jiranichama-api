"""Automatic chasing (owner request 2026-10-21). Nothing waits quietly: the right people
are reminded, once each, and a withdrawal nobody explains becomes a dispute by itself.

Hourly (``chase_hourly``):
- an approval about to run out reminds the officials who have not answered;
- a vote about to close reminds the members who have not voted;
- money marked Paid but not in the bank after 3 days tells the officials, after 7 days
  every member;
- cash a member confirmed but not banked after 7 days tells the treasurer and every member;
- cash recorded for a member who has not confirmed it after 2 days reminds them;
- a bank statement waiting more than a day for a second official reminds the others;
- minutes not written 3 days after a meeting remind the secretary;
- money that left the bank with no approval and no explanation for 72 hours opens a
  dispute (5.7, 5.14).

Daily (``chase_daily``): the treasurer hears how many payments wait to be matched.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.chamas.models import OFFICIAL_ROLES, Chama, Membership
from apps.core import money

from .notices import notify

PAID_UNSEEN_OFFICIALS = timedelta(days=3)
PAID_UNSEEN_EVERYONE = timedelta(days=7)
CASH_UNBANKED_DAYS = 7
CASH_UNCONFIRMED = timedelta(days=2)
STATEMENT_WAITING = timedelta(days=1)
MINUTES_DUE = timedelta(days=3)
DEBIT_TO_DISPUTE = timedelta(hours=72)
SOON = timedelta(hours=24)


def _when(dt) -> str:
    return timezone.localtime(dt).strftime("%d %b %H:%M")


def _day(d) -> str:
    return d.strftime("%d %b")


def _officials(chama: Chama):
    return Membership.objects.filter(chama=chama, role__in=OFFICIAL_ROLES,
                                     state=Membership.State.ACTIVE).select_related("user")


def _current(chama: Chama):
    return Membership.objects.filter(
        chama=chama, state__in=[Membership.State.ACTIVE, Membership.State.PROBATION]
    ).select_related("user")


def _name(m: Membership) -> str:
    return m.user.display_name or m.member_code


# --- Approvals and votes about to run out ----------------------------------------------------

def approvals_soon(now) -> int:
    from apps.payouts.models import Approval, Proposal
    from apps.payouts.services import eligible_approvers

    n = 0
    for p in (Proposal.objects.filter(state=Proposal.State.COLLECTING,
                                      deadline_at__gt=now, deadline_at__lte=now + SOON)
              .select_related("chama")):
        answered = set(Approval.objects.filter(proposal=p).values_list("membership_id",
                                                                       flat=True))
        amount = money.fmt(p.amount_cents, p.chama)
        for o in eligible_approvers(p).select_related("user"):
            if o.id in answered:
                continue
            if notify(o.user, chama=p.chama, kind="approve", key=f"approve-soon:{p.id}",
                      sms="always", urgent=True,
                      data={"screen": "money_out", "id": str(p.id)},
                      text={"en": f"Approve or decline paying {p.payee_name} {amount} for "
                                  f"{p.purpose}. It closes {_when(p.deadline_at)}.",
                            "sw": f"Idhinisha au kataa kumlipa {p.payee_name} {amount} kwa "
                                  f"{p.purpose}. Inafungwa {_when(p.deadline_at)}."}):
                n += 1
    return n


def votes_soon(now) -> int:
    from apps.votes.models import Turnout, Vote, VoteSmsNumber

    n = 0
    for v in Vote.objects.filter(state=Vote.State.OPEN, closes_at__gt=now,
                                 closes_at__lte=now + SOON).select_related("chama"):
        voted = set(Turnout.objects.filter(vote=v).values_list("membership_id", flat=True))
        numbers = dict(VoteSmsNumber.objects.filter(vote=v)
                       .values_list("membership_id", "number"))
        for m in Membership.objects.filter(pk__in=v.eligible_ids).select_related("user"):
            if m.id in voted:
                continue
            number = numbers.get(m.id)
            if number:  # a basic phone: the reply that votes
                text = {"en": f"Last day to vote: {v.question} Reply YES {number} or NO "
                              f"{number} by {_when(v.closes_at)}.",
                        "sw": f"Siku ya mwisho kupiga kura: {v.question} Jibu NDIYO "
                              f"{number} au HAPANA {number} kabla ya {_when(v.closes_at)}."}
            else:
                text = {"en": f"You have not voted yet: {v.question} It closes "
                              f"{_when(v.closes_at)}.",
                        "sw": f"Bado hujapiga kura: {v.question} Inafungwa "
                              f"{_when(v.closes_at)}."}
            if notify(m.user, chama=v.chama, kind="vote", key=f"vote-soon:{v.id}",
                      sms="always" if number else "away", urgent=True,
                      data={"screen": "vote", "id": str(v.id)}, text=text):
                n += 1
    return n


# --- Money marked paid but not in the bank -------------------------------------------------

def paid_unseen(now) -> int:
    from apps.payouts.models import Proposal

    n = 0
    for p in (Proposal.objects.filter(state=Proposal.State.PAID_PENDING_MATCH,
                                      paid_at__lte=now - PAID_UNSEEN_OFFICIALS)
              .select_related("chama")):
        amount = money.fmt(p.amount_cents, p.chama)
        everyone = p.paid_at <= now - PAID_UNSEEN_EVERYONE
        people = _current(p.chama) if everyone else _officials(p.chama)
        text = {"en": f"{amount} to {p.payee_name} was marked paid on {_day(p.paid_at)} but "
                      "is not in the bank records yet. Officials: share the latest bank "
                      "statement so it can be checked.",
                "sw": f"{amount} kwa {p.payee_name} ilionyeshwa imelipwa tarehe "
                      f"{_day(p.paid_at)} lakini bado haionekani benki. Viongozi: leteni "
                      "taarifa ya benki ya karibuni ili ikaguliwe."}
        key = f"paid-unseen-{'all' if everyone else 'officials'}:{p.id}"
        for m in people:
            if notify(m.user, chama=p.chama, kind="money_out", key=key, text=text,
                      sms="away", data={"screen": "money_out", "id": str(p.id)}):
                n += 1
    return n


# --- Cash at meetings -----------------------------------------------------------------------

def cash_unbanked(today) -> int:
    from apps.contributions.models import CashReceipt

    n = 0
    cutoff = today - timedelta(days=CASH_UNBANKED_DAYS)
    for r in (CashReceipt.objects.filter(state=CashReceipt.State.MEMBER_CONFIRMED,
                                         received_on__lte=cutoff)
              .select_related("chama", "membership__user")):
        amount = money.fmt(r.amount_cents, r.chama)
        text = {"en": f"Cash of {amount} that {_name(r.membership)} handed over on "
                      f"{_day(r.received_on)} is still not in the bank after "
                      f"{CASH_UNBANKED_DAYS} days.",
                "sw": f"Pesa taslimu {amount} alizotoa {_name(r.membership)} tarehe "
                      f"{_day(r.received_on)} bado hazijawekwa benki baada ya siku "
                      f"{CASH_UNBANKED_DAYS}."}
        for m in _current(r.chama):
            urgent = m.role == Membership.Role.TREASURER
            if notify(m.user, chama=r.chama, kind="cash", key=f"cash-unbanked:{r.id}",
                      text=text, sms="always" if urgent else "away", urgent=urgent,
                      data={"screen": "cash", "id": str(r.id)}):
                n += 1
    return n


def cash_unconfirmed(now) -> int:
    from apps.contributions.models import CashReceipt

    n = 0
    for r in (CashReceipt.objects.filter(state=CashReceipt.State.RECORDED,
                                         created_at__lte=now - CASH_UNCONFIRMED)
              .select_related("chama", "membership__user")):
        amount = money.fmt(r.amount_cents, r.chama)
        if notify(r.membership.user, chama=r.chama, kind="cash", key=f"cash-confirm:{r.id}",
                  sms="always", data={"screen": "cash", "id": str(r.id)},
                  text={"en": f"The treasurer recorded {amount} cash from you on "
                              f"{_day(r.received_on)}. Open JiraniChama to confirm it, or "
                              "say it is wrong.",
                        "sw": f"Mweka hazina ameandika pesa taslimu {amount} kutoka kwako "
                              f"tarehe {_day(r.received_on)}. Fungua JiraniChama uthibitishe, "
                              "au useme si sahihi."}):
            n += 1
    return n


# --- Statements and minutes waiting for someone ---------------------------------------------

def statements_waiting(now) -> int:
    from apps.feeds.models import Statement

    n = 0
    for s in (Statement.objects.filter(state=Statement.State.AWAITING_CONFIRMATION,
                                       created_at__lte=now - STATEMENT_WAITING)
              .select_related("chama")):
        for o in _officials(s.chama):
            if s.uploaded_by_id and o.user_id == s.uploaded_by_id:
                continue  # someone else confirms
            if notify(o.user, chama=s.chama, kind="statement", key=f"statement-wait:{s.id}",
                      sms="always", data={"screen": "statements", "id": str(s.id)},
                      text={"en": "A bank statement is waiting for a second official to "
                                  "check it. Open JiraniChama to confirm it.",
                            "sw": "Taarifa ya benki inasubiri kiongozi wa pili aikague. "
                                  "Fungua JiraniChama uithibitishe."}):
                n += 1
    return n


def minutes_due(now) -> int:
    from apps.meetings.models import Meeting
    from apps.meetings.services import runs_meetings

    n = 0
    for mt in (Meeting.objects.filter(state=Meeting.State.ATTENDANCE_CLOSED,
                                      starts_at__lte=now - MINUTES_DUE)
               .select_related("chama")):
        for o in _officials(mt.chama):
            if not runs_meetings(o):
                continue
            if notify(o.user, chama=mt.chama, kind="meeting", key=f"minutes-due:{mt.id}",
                      sms="always", data={"screen": "meeting", "id": str(mt.id)},
                      text={"en": f"The minutes of \"{mt.title}\" are not written yet. "
                                  "Write them or add a photo in JiraniChama.",
                            "sw": f"Kumbukumbu za \"{mt.title}\" bado hazijaandikwa. "
                                  "Ziandike au weka picha kwenye JiraniChama."}):
                n += 1
    return n


# --- A withdrawal nobody explains -------------------------------------------------------------

def debits_to_disputes(now) -> int:
    from apps.disputes import services as disputes
    from apps.disputes.models import Dispute
    from apps.payouts.models import UnapprovedDebit

    n = 0
    for d in (UnapprovedDebit.objects.filter(state=UnapprovedDebit.State.OPEN,
                                             explanation="",
                                             created_at__lte=now - DEBIT_TO_DISPUTE)
              .select_related("chama", "bank_txn")):
        if Dispute.objects.filter(chama=d.chama, subject_type=Dispute.Subject.PAYMENT,
                                  subject_id=d.bank_txn_id).exists():
            continue
        amount = money.fmt(d.bank_txn.amount_cents, d.chama)
        with transaction.atomic():
            disputes.raise_automatic(
                d.chama, subject_type=Dispute.Subject.PAYMENT, subject_id=d.bank_txn_id,
                reason=Dispute.Reason.NOT_APPROVED,
                text=f"{amount} left the bank on {_day(d.bank_txn.value_date)} with no "
                     "approval, and no official explained it within 72 hours. "
                     "JiraniChama opened this dispute automatically.")
        n += 1
    return n


def chase_hourly(now=None) -> dict:
    now = now or timezone.now()
    today = timezone.localdate(now)
    return {"approvals": approvals_soon(now), "votes": votes_soon(now),
            "paid_unseen": paid_unseen(now), "cash_unbanked": cash_unbanked(today),
            "cash_unconfirmed": cash_unconfirmed(now),
            "statements": statements_waiting(now), "minutes": minutes_due(now),
            "disputes": debits_to_disputes(now)}


# --- Daily: payments waiting to be matched ------------------------------------------------------

def unmatched_digest(today=None) -> int:
    from django.db.models import Count, Sum

    from apps.feeds.models import BankTransaction

    today = today or timezone.localdate()
    n = 0
    rows = (BankTransaction.objects.filter(state=BankTransaction.State.UNMATCHED,
                                           direction=BankTransaction.Direction.CREDIT,
                                           created_at__date__lt=today)
            .values("chama").annotate(n=Count("id"), t=Sum("amount_cents")))
    for row in rows:
        chama = Chama.objects.get(pk=row["chama"])
        total = money.fmt(row["t"] or 0, chama)
        for t in _officials(chama).filter(role=Membership.Role.TREASURER):
            if notify(t.user, chama=chama, kind="unmatched",
                      key=f"unmatched:{today.isoformat()}", sms="away",
                      data={"screen": "unmatched"},
                      text={"en": f"{row['n']} payments ({total}) are waiting to be matched "
                                  "to members. JiraniChama has suggestions ready.",
                            "sw": f"Malipo {row['n']} ({total}) yanasubiri kuhusishwa na "
                                  "wanachama. JiraniChama ina mapendekezo tayari."}):
                n += 1
    return n


def chase_daily(today=None) -> dict:
    return {"unmatched": unmatched_digest(today)}
