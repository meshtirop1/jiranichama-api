"""Changing the rules by members' vote (5.10)."""

import copy
import re
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.problems import Problem
from apps.ledger.models import LedgerEntry
from apps.rules import proposals
from apps.rules.models import RuleProposal, RuleVersion, version_at
from apps.rules.templates import template_for
from apps.votes import services as votes

from .phase1 import active_chama, idem

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


def _new_rules(**contrib):
    r = copy.deepcopy(template_for("merry_go_round"))
    r["contributions"].update(contrib)
    return r


def _vote(m, v, choice, outbox):
    ch = votes.start_sms_ballot(v, m, choice)
    return votes.cast_sms(v, m, choice=choice, challenge_id=ch.id, sms_code=_code(outbox))


def _past_notice(monkeypatch, p):
    later = p.notice_until + timedelta(minutes=1)
    monkeypatch.setattr(proposals.timezone, "now", lambda: later)
    proposals.advance(p.chama)
    monkeypatch.undo()
    p.refresh_from_db()
    return votes.for_subject(f"rules.proposal:{p.id}")


def test_a_change_is_proposed_shown_and_put_on_notice():
    c, (chair, *_) = active_chama(since=TODAY)
    p = proposals.propose(c, chair, rules=_new_rules(amount_cents=150_000),
                          reason="Prices went up")
    assert p.state == "notice" and p.changed == ["contributions/amount_cents"]
    assert p.notice_until.date() == (timezone.now() + timedelta(days=14)).date()
    [clause] = proposals.changed_clauses(p.base_version.rules, p.rules, "en")
    assert clause["ref"] == "3.1" and "KES 1,000" in clause["before"]
    assert "KES 1,500" in clause["after"]
    assert (p.quorum_pct, p.pass_pct) == (50, 66)


def test_bad_or_empty_changes_and_non_officials_are_refused():
    c, (chair, *_) = active_chama(since=TODAY, members=4)
    member = c.memberships.get(role="member")
    with pytest.raises(Problem) as e:
        proposals.propose(c, chair, rules=_new_rules(), reason="Nothing")
    assert e.value.problem_code == "no_change"
    with pytest.raises(Problem) as e:
        proposals.propose(c, chair, rules=_new_rules(due_day=31), reason="x")
    assert e.value.problem_code == "rules_invalid"
    with pytest.raises(Problem) as e:
        proposals.propose(c, member, rules=_new_rules(amount_cents=150_000), reason="x")
    assert e.value.problem_code == "not_allowed"


def test_two_changes_to_the_same_rules_wait_their_turn():
    c, (chair, treasurer, _) = active_chama(since=TODAY)
    proposals.propose(c, chair, rules=_new_rules(amount_cents=150_000), reason="a")
    with pytest.raises(Problem) as e:
        proposals.propose(c, treasurer, rules=_new_rules(due_day=10), reason="b")
    assert e.value.problem_code == "proposal_open"


def test_adopted_after_notice_and_vote_from_the_first_of_next_month(monkeypatch, sms_outbox):
    c, (chair, treasurer, secretary) = active_chama(since=TODAY)
    p = proposals.propose(c, chair, rules=_new_rules(amount_cents=150_000), reason="Up")
    v = _past_notice(monkeypatch, p)
    assert p.state == "voting" and v.state == "open"
    assert (v.quorum_pct, v.pass_pct, v.eligible) == (50, 66, 3)
    _vote(chair, v, "yes", sms_outbox)
    _vote(treasurer, v, "yes", sms_outbox)
    v = _vote(secretary, v, "yes", sms_outbox)
    p.refresh_from_db()
    assert v.state == "passed" and p.state == "adopted"
    new = p.new_version
    first = (TODAY.replace(day=1) + timedelta(days=32)).replace(day=1)
    assert new.state == "scheduled" and new.effective_from == first
    assert version_at(c, TODAY).version_no == 1  # never backwards
    assert LedgerEntry.objects.filter(chama=c, type="rule_version_adopted",
                                      source_id=p.id).exists()
    assert proposals.put_in_force(first) == 1
    assert version_at(c, first).rules["contributions"]["amount_cents"] == 150_000
    assert RuleVersion.objects.get(chama=c, version_no=1).state == "superseded"
    p.refresh_from_db()
    assert p.state == "in_force"


def test_two_thirds_of_all_members_are_needed_and_a_rejected_change_cools_off(
        monkeypatch, sms_outbox):
    c, ms = active_chama(since=TODAY, members=6)
    chair, treasurer, secretary, a, b, d = ms
    rules = _new_rules(amount_cents=150_000)
    p = proposals.propose(c, chair, rules=rules, reason="Up")
    v = _past_notice(monkeypatch, p)
    assert v.over_all and v.quorum_pct >= 50 and v.pass_pct >= 66
    # 4 of 6 is two-thirds of the voters AND of all members, but 66.7% > 66 only just:
    for m in (chair, treasurer, secretary, a):
        _vote(m, v, "yes", sms_outbox)
    _vote(b, v, "no", sms_outbox)
    v = _vote(d, v, "no", sms_outbox)
    assert v.state == "passed"

    q_rules = _new_rules(due_day=10)
    q = proposals.propose(c, chair, rules=q_rules, reason="Later")
    v = _past_notice(monkeypatch, q)
    # Three for and three against: a tie never changes anything, and it is recorded as one.
    for m in (chair, treasurer, secretary):
        _vote(m, v, "yes", sms_outbox)
    _vote(a, v, "no", sms_outbox)
    _vote(b, v, "no", sms_outbox)
    v = _vote(d, v, "no", sms_outbox)
    assert v.state == "failed"
    from apps.ledger.models import LedgerEntry

    assert LedgerEntry.objects.get(pk=v.result_entry_id).decision["tied"] is True
    q.refresh_from_db()
    assert q.state == "rejected"
    with pytest.raises(Problem) as e:
        proposals.propose(c, chair, rules=q_rules, reason="Again")
    assert e.value.problem_code == "cooling_off"


def test_the_rule_change_rule_changes_only_under_the_stricter_threshold():
    c, (chair, *_) = active_chama(since=TODAY)
    r = copy.deepcopy(template_for("merry_go_round"))
    r["rule_changes"].update(pass_pct=50, quorum_pct=25)  # trying to make it easier
    p = proposals.propose(c, chair, rules=r, reason="Easier changes")
    assert (p.quorum_pct, p.pass_pct) == (50, 66)


def test_no_notice_means_the_vote_opens_at_once():
    rules = template_for("merry_go_round")
    rules["rule_changes"]["notice_days"] = 0
    c, (chair, *_) = active_chama(since=TODAY, rules=rules)
    new = copy.deepcopy(rules)
    new["contributions"]["grace_days"] = 4
    p = proposals.propose(c, chair, rules=new, reason="More grace")
    p.refresh_from_db()
    assert p.state == "voting" and votes.for_subject(f"rules.proposal:{p.id}") is not None


def test_withdraw_during_notice_only(api):
    c, (chair, treasurer, _) = active_chama(since=TODAY)
    p = proposals.propose(c, chair, rules=_new_rules(amount_cents=150_000), reason="Up")
    with pytest.raises(Problem):
        proposals.withdraw(p, treasurer)
    api.force_authenticate(chair.user)
    r = api.post(f"/api/v1/rule-proposals/{p.id}/withdraw/", {}, format="json")
    assert r.status_code == 200 and r.json()["state"] == "withdrawn"


def test_rule_changes_through_the_api(api):
    c, (chair, *_) = active_chama(since=TODAY)
    api.force_authenticate(chair.user)
    r = api.post(f"/api/v1/chamas/{c.id}/rule-proposals/",
                 {"rules": _new_rules(amount_cents=150_000), "reason": "Up"}, format="json",
                 **idem())
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["clauses"]["sw"][0]["ref"] == "3.1" and body["state"] == "notice"
    assert api.get(f"/api/v1/chamas/{c.id}/rule-proposals/").json()[0]["id"] == body["id"]
    assert RuleProposal.objects.count() == 1
