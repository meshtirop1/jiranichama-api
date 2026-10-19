"""SMS commands for members on basic phones, and voting by SMS reply (4.9, 5.11)."""

import base64
import re
from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.chamas.models import Membership
from apps.feeds import services as feeds
from apps.fines.services import raise_late_fines
from apps.ledger.models import LedgerEntry
from apps.notify.commands import handle
from apps.notify.models import InboundSms
from apps.payouts import services as payouts
from apps.reconciliation import services as recon
from apps.rules.templates import template_for
from apps.votes import services as votes
from apps.votes.models import VoteSmsNumber

from .phase1 import active_chama, credit, make_user, statement_csv

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _phone(m: Membership) -> str:
    return m.user.phone


def _last(outbox):
    return outbox[-1][1]


def test_bal_due_fines_and_rules_answer_by_sms(sms_outbox):
    c, (chair, treasurer, _) = active_chama(since=date(2026, 1, 1))
    raise_late_fines(c, date(2026, 2, 10))
    recon.match_credit(credit(c, amount_cents=100_000, value_date=date(2026, 2, 3),
                              narration=f"pay {treasurer.member_code}"))

    handle(_phone(treasurer), "BAL")
    assert _last(sms_outbox).startswith("JiraniChama: Test Chama: saved KES 1,000")
    handle(_phone(treasurer), "fines")
    assert "fine(s)" in _last(sms_outbox)
    handle(_phone(treasurer), "DUE")
    assert "by" in _last(sms_outbox) and treasurer.member_code in _last(sms_outbox)
    handle(_phone(treasurer), "RULES")
    assert "KES 1,000 a month by day 5" in _last(sms_outbox)
    handle(_phone(treasurer), "whatever")
    assert "Send: BAL, DUE" in _last(sms_outbox)
    assert InboundSms.objects.filter(user=treasurer.user, replied=True).count() == 5


def test_kiswahili_words_answer_in_kiswahili(sms_outbox):
    c, (chair, *_) = active_chama(since=TODAY)
    handle(_phone(chair), "SALIO")
    assert "akiba" in _last(sms_outbox)
    handle(_phone(chair), "msaada")
    assert "Tuma: SALIO" in _last(sms_outbox)


def test_unknown_numbers_get_no_answer_and_numbers_are_limited(sms_outbox, settings):
    c, (chair, *_) = active_chama(since=TODAY)
    before = len(sms_outbox)
    assert handle("+254700999888", "BAL") is None
    assert len(sms_outbox) == before
    settings.SMS_COMMANDS_PER_HOUR = 2
    handle(_phone(chair), "BAL")
    handle(_phone(chair), "BAL")
    assert handle(_phone(chair), "BAL") is None  # third in the hour
    assert InboundSms.objects.filter(command="limited").count() == 1


def test_verify_a_receipt_in_your_chama_but_not_anyone_elses(sms_outbox):
    c, (chair, treasurer, _) = active_chama(since=TODAY)
    other, (stranger, *_) = active_chama(since=TODAY)
    recon.match_credit(credit(c, amount_cents=100_000, value_date=TODAY,
                              narration=f"pay {treasurer.member_code}"))
    entry = LedgerEntry.objects.filter(chama=c, type="contribution").first()
    handle(_phone(chair), f"VERIFY {entry.receipt_code.lower()}")
    assert _last(sms_outbox).startswith("JiraniChama: GENUINE: contribution KES 1,000")
    handle(_phone(stranger), f"VERIFY {entry.receipt_code}")
    assert "No record has that code" in _last(sms_outbox)


def test_a_member_without_the_app_votes_by_replying(settings, sms_outbox,
                                                    django_capture_on_commit_callbacks):
    settings.BANK_STATEMENT_SENDERS = {"equity": ["equitybank.example"]}
    rules = template_for("merry_go_round")
    c, (chair, treasurer, secretary) = active_chama(since=TODAY, rules=rules)
    mary = Membership.objects.create(chama=c, user=make_user("+254799000951", "Mary"),
                                     joined_on=TODAY, member_code=f"{c.member_code_prefix}-004")
    john = Membership.objects.create(chama=c, user=make_user("+254799000952", "John"),
                                     joined_on=TODAY, member_code=f"{c.member_code_prefix}-005")
    data = statement_csv([(TODAY - timedelta(days=1), "DEPOSIT", 0, 10_000_000)],
                         opening_cents=0)
    feeds.receive_mail({
        "to": c.statement_inbox.address, "from": "s@equitybank.example",
        "auth": {"spf": "pass", "dkim": "pass", "dmarc": "pass",
                 "dkim_domain": "equitybank.example"},
        "attachments": [{"filename": "s.csv", "content_b64": base64.b64encode(data).decode()}]})
    for o in (chair, treasurer):
        ch = payouts.start_enable(c, o)
        payouts.enable(c, o, challenge_id=ch.id,
                       sms_code=re.search(r"\b(\d{6})\b", _last(sms_outbox)).group(1))
    c.refresh_from_db()
    with django_capture_on_commit_callbacks(execute=True):
        p = payouts.propose(c, chair, kind="payout", amount_cents=6_000_000, payee=mary,
                            purpose="Hospital bill")
    v = votes.for_subject(f"payouts.proposal:{p.id}")
    invite = [t for to, t in sms_outbox if to == john.user.phone][-1]
    n = VoteSmsNumber.objects.get(vote=v, membership=john).number
    assert f"Reply YES {n} or NO {n}" in invite and "Pay Mary KES 60,000" in invite
    assert not any(to == mary.user.phone and "vote" in t for to, t in sms_outbox)  # payee

    handle(john.user.phone, f"YES {n}")
    assert "Your vote YES is recorded" in _last(sms_outbox)
    handle(john.user.phone, f"NO {n}")
    assert "already voted" in _last(sms_outbox)
    handle(secretary.user.phone, f"NDIYO {n}")  # someone else's number: not theirs
    assert "Nambari ya kura haijulikani" in _last(sms_outbox)
    v.refresh_from_db()
    assert v.ballots.get().method == "reply"
