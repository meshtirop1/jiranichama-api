"""Smart matching (2026-10-21): learned payers match by themselves once the member agrees;
other payments come with suggestions the treasurer confirms with one tap."""

import uuid

import pytest
from django.utils import timezone

from apps.accounts.phones import phone_hash
from apps.automation.models import Notice
from apps.chamas.models import Membership
from apps.chamas.services import active_bank_account
from apps.core.problems import Problem
from apps.feeds.models import BankTransaction
from apps.reconciliation import matching
from apps.reconciliation import services as recon
from apps.reconciliation.models import Match, PayerLink

from .phase1 import active_chama, credit, make_user

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()
HUSBAND = "+254722000999"


def _paid_from(chama, phone_h, cents=100_000, narration="M-PESA QX1 JOHN KAMAU"):
    """An M-Pesa payment from a phone (bank lines are insert only, so set it at once)."""
    txn = BankTransaction(chama=chama, bank_account=active_bank_account(chama),
                          source="daraja", external_ref=uuid.uuid4().hex[:10].upper(),
                          direction="credit", amount_cents=cents, value_date=TODAY,
                          payer_phone_hash=phone_h, state=BankTransaction.State.UNMATCHED)
    txn.set_narration(narration)
    txn.save()
    recon.match_credit(txn)
    txn.refresh_from_db()
    return txn


def _from_husband(chama, cents=100_000):
    return _paid_from(chama, phone_hash(HUSBAND), cents)


@pytest.fixture
def chama():
    c, (chair, treasurer, secretary) = active_chama(since=TODAY)
    mary = Membership.objects.create(chama=c, user=make_user("+254799000701", "Mary Wanjiku"),
                                     joined_on=TODAY, member_code=f"{c.member_code_prefix}-004")
    return c, treasurer, mary


def test_a_payer_the_member_confirms_matches_by_itself_next_time(
        chama, sms_outbox, django_capture_on_commit_callbacks):
    c, treasurer, mary = chama
    txn = _from_husband(c)
    assert txn.state == "unmatched"
    with django_capture_on_commit_callbacks(execute=True):
        recon.assign(txn, mary, by=treasurer, reason="Her husband's phone")
    link = PayerLink.objects.get(chama=c)
    assert link.state == "suggested" and link.membership_id == mary.id
    assert Notice.objects.filter(user=mary.user, key=f"payer-link:{link.id}").exists()
    # Not yet: a suggested link does not match.
    assert _from_husband(c).state == "unmatched"
    matching.answer(link, mary, yes=True)
    txn3 = _from_husband(c, cents=50_000)
    assert txn3.state == "recorded"
    assert txn3.matches.get().method == Match.Method.PAYER_LINK
    # The member ends it: payments wait for the treasurer again.
    matching.end(link, mary)
    assert _from_husband(c).state == "unmatched"


def test_a_declined_payer_is_never_matched_automatically(chama):
    c, treasurer, mary = chama
    recon.assign(_from_husband(c), mary, by=treasurer, reason="Assigned")
    link = PayerLink.objects.get(chama=c)
    matching.answer(link, mary, yes=False)
    assert _from_husband(c).state == "unmatched"


def test_only_the_member_answers_and_a_members_own_phone_is_never_a_payer(chama):
    c, treasurer, mary = chama
    recon.assign(_from_husband(c), mary, by=treasurer, reason="Assigned")
    link = PayerLink.objects.get(chama=c)
    with pytest.raises(Problem):
        matching.answer(link, treasurer, yes=True)
    # A payment from the treasurer's own phone matches the treasurer (their phone), and an
    # assignment of it to someone else does not teach a payer link.
    txn = _paid_from(c, treasurer.user.phone_hash, 10_000, "DEPOSIT")
    assert txn.state == "recorded"
    assert matching.learn_from_assignment(txn, mary) is None


def test_suggestions_name_their_reasons(chama, api):
    c, treasurer, mary = chama
    prefix = c.member_code_prefix.replace("JC-", "")
    # Mary's code with two digits swapped, and her first name.
    txn = credit(c, amount_cents=100_000, value_date=TODAY,
                 narration=f"DEPOSIT JC {prefix} 040 MARY")
    [top, *_] = matching.suggest(txn)
    assert top["membership_id"] == str(mary.id)
    assert set(top["reasons"]) >= {"code", "name"}
    api.force_authenticate(treasurer.user)
    r = api.get(f"/api/v1/chamas/{c.id}/unmatched/")
    assert r.status_code == 200
    assert r.data[0]["suggestions"][0]["member_code"] == mary.member_code
    assert r.data[0]["suggestions"][0]["reason_text"].startswith("Suggested:")


def test_payer_links_through_the_api(chama, api):
    c, treasurer, mary = chama
    recon.assign(_from_husband(c), mary, by=treasurer, reason="Assigned")
    link = PayerLink.objects.get(chama=c)
    api.force_authenticate(mary.user)
    assert len(api.get(f"/api/v1/chamas/{c.id}/payer-links/").data["links"]) == 1
    r = api.post(f"/api/v1/payer-links/{link.id}/answer/", {"yes": True}, format="json")
    assert r.status_code == 200 and r.data["state"] == "confirmed"
    api.force_authenticate(treasurer.user)
    r = api.post(f"/api/v1/payer-links/{link.id}/end/")
    assert r.status_code == 200 and r.data["state"] == "ended"
