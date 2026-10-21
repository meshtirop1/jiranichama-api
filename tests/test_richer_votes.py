"""Richer voting (owner 2026-10-21: "the voting system is too basic"): abstaining,
changing a vote, members' motions with seconders, polls with options, comments, and SMS
replies for all of it."""

import re
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.problems import Problem
from apps.ledger.models import LedgerEntry
from apps.notify.commands import answer
from apps.votes import services as votes
from apps.votes.models import Ballot, Motion, Turnout, VoteSmsNumber

from .phase1 import active_chama

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


def _vote(v, m, choice, outbox):
    ch = votes.start_sms_ballot(v, m, choice)
    return votes.cast_sms(v, m, choice=choice, challenge_id=ch.id, sms_code=_code(outbox))


@pytest.fixture
def seven():
    c, ms = active_chama(since=TODAY, members=7)
    return c, ms


def _motion_to_vote(c, ms, **kw):
    m = votes.raise_motion(c, ms[3], **kw)
    assert m.state == "seconding" and m.seconds_needed == 2
    votes.second(m, ms[4])
    m = votes.second(m, ms[5])
    assert m.state == "voting" and m.vote.allow_change
    return m


def test_a_motion_needs_two_seconds_and_never_its_own(seven):
    c, ms = seven
    m = votes.raise_motion(c, ms[3], question="Shall we buy plastic chairs for meetings?")
    with pytest.raises(Problem) as e:
        votes.second(m, ms[3])
    assert e.value.problem_code == "own_motion"
    m = votes.second(m, ms[4])
    assert m.state == "seconding"
    with pytest.raises(Problem):
        votes.second(m, ms[4])  # once each
    m = votes.second(m, ms[5])
    assert m.state == "voting" and m.vote.eligible == 7


def test_abstaining_counts_as_taking_part_but_not_against(seven, sms_outbox):
    c, ms = seven
    m = _motion_to_vote(c, ms, question="Shall we meet on Saturdays?")
    v = m.vote
    for member, choice in zip(ms, ["yes", "yes", "yes", "no", "abstain", "abstain"], strict=False):
        v = _vote(v, member, choice, sms_outbox)
    v = votes.close_due(c) or v
    v.refresh_from_db()
    assert v.state == "open"  # still open: one member has not voted
    v = _vote(v, ms[6], "abstain", sms_outbox)
    assert v.state == "passed"  # 3 yes against 1 no; 3 abstained
    assert (v.yes, v.no, v.abstain) == (3, 1, 3)
    entry = LedgerEntry.objects.get(pk=v.result_entry_id)
    assert entry.decision["abstain"] == 3 and entry.decision["raised_by"] == ms[3].member_code
    m.refresh_from_db()
    assert m.state == "passed"


def test_a_member_may_change_their_vote_until_it_closes(seven, sms_outbox):
    c, ms = seven
    v = _motion_to_vote(c, ms, question="Shall we buy a tent?").vote
    _vote(v, ms[0], "no", sms_outbox)
    _vote(v, ms[0], "yes", sms_outbox)
    assert Turnout.objects.filter(vote=v).count() == 1
    assert sorted(Ballot.objects.filter(vote=v).values_list("round", flat=True)) == [1, 2]
    assert votes.my_ballot(v, ms[0]).choice == "yes"
    for member in ms[1:4]:
        _vote(v, member, "no", sms_outbox)
    for member in ms[4:]:
        v = _vote(v, member, "yes", sms_outbox)
    assert v.state == "passed" and (v.yes, v.no) == (4, 3)  # the changed vote counts


def test_votes_other_flows_open_still_count_the_first_ballot(seven, sms_outbox):
    c, ms = seven
    from apps.rules.templates import template_for

    v = votes.open_vote(c, subject="test:first", question="Pay?",
                        rules=template_for("merry_go_round"),
                        closes_at=timezone.now() + timedelta(days=1))
    assert not v.allow_change
    _vote(v, ms[0], "yes", sms_outbox)
    with pytest.raises(Problem) as e:
        votes.start_sms_ballot(v, ms[0], "no")
    assert e.value.problem_code == "already_voted"


def test_a_poll_is_won_by_the_option_with_most_votes(seven, sms_outbox):
    c, ms = seven
    m = _motion_to_vote(c, ms, kind="choice", question="Where shall the AGM be?",
                        options=["Church hall", "School", "Chair's home"])
    v = m.vote
    assert v.kind == "choice" and v.options == ["Church hall", "School", "Chair's home"]
    with pytest.raises(Problem):
        votes.start_sms_ballot(v, ms[0], "yes")  # a poll takes an option
    for member, choice in zip(ms, ["2", "2", "1", "2", "3", "abstain", "1"], strict=True):
        v = _vote(v, member, choice, sms_outbox)
    assert v.state == "passed" and v.winner == 2
    assert v.option_counts == {"1": 2, "2": 3, "3": 1}
    entry = LedgerEntry.objects.get(pk=v.result_entry_id)
    assert entry.decision["winner"] == 2 and entry.decision["options"][1] == "School"


def test_a_tie_at_the_top_decides_nothing(seven, sms_outbox):
    c, ms = seven
    v = _motion_to_vote(c, ms, kind="choice", question="Which day?",
                        options=["Saturday", "Sunday"]).vote
    for member, choice in zip(ms, ["1", "1", "1", "2", "2", "2", "abstain"], strict=True):
        v = _vote(v, member, choice, sms_outbox)
    assert v.state == "failed" and v.winner is None
    assert LedgerEntry.objects.get(pk=v.result_entry_id).decision["tied"]


def test_basic_phones_vote_in_polls_and_abstain_by_sms(seven, sms_outbox,
                                                       django_capture_on_commit_callbacks):
    c, ms = seven
    m = votes.raise_motion(c, ms[3], kind="choice", question="Where shall the AGM be?",
                           options=["Church hall", "School"])
    votes.second(m, ms[4])
    with django_capture_on_commit_callbacks(execute=True):
        m = votes.second(m, ms[5])
    n0 = VoteSmsNumber.objects.get(vote=m.vote, membership=ms[0]).number
    invite = [t for to, t in sms_outbox if to == ms[0].user.phone][-1]
    assert f"1 {n0} (Church hall)" in invite and f"2 {n0} (School)" in invite
    _, reply = answer(ms[0].user, f"2 {n0}")
    assert "Your vote 2 (School) is recorded" in reply
    n1 = VoteSmsNumber.objects.get(vote=m.vote, membership=ms[1]).number
    _, reply = answer(ms[1].user, f"ABSTAIN {n1}")
    assert "ABSTAIN" in reply
    n2 = VoteSmsNumber.objects.get(vote=m.vote, membership=ms[2]).number
    _, reply = answer(ms[2].user, f"9 {n2}")
    assert "not in this vote" in reply


def test_members_comment_while_a_vote_is_open(seven, sms_outbox, api):
    c, ms = seven
    v = _motion_to_vote(c, ms, question="Shall we buy a tent?").vote
    api.force_authenticate(ms[1].user)
    r = api.post(f"/api/v1/votes/{v.id}/comments/", {"text": "Tents rot. Chairs last."},
                 format="json")
    assert r.status_code == 201
    r = api.get(f"/api/v1/votes/{v.id}/")
    assert r.data["comments"] == 1 and r.data["raised_by"] == ms[3].user.display_name
    assert api.get(f"/api/v1/votes/{v.id}/comments/").data[0]["text"] == "Tents rot. Chairs last."
    for member in ms:
        v = _vote(v, member, "yes", sms_outbox)
    assert v.state == "passed"
    with pytest.raises(Problem):
        votes.comment(v, ms[1], "Too late")


def test_motions_through_the_api_and_ones_nobody_seconds_expire(seven, api, monkeypatch):
    c, ms = seven
    api.force_authenticate(ms[3].user)
    r = api.post(f"/api/v1/chamas/{c.id}/motions/",
                 {"kind": "choice", "question": "Which bank for our second account?",
                  "options": ["Equity", "KCB", "Co-op"], "vote_days": 3}, format="json")
    assert r.status_code == 201 and r.data["state"] == "seconding" and r.data["mine"]
    mid = r.data["id"]
    api.force_authenticate(ms[4].user)
    rows = api.get(f"/api/v1/chamas/{c.id}/motions/").data
    assert rows[0]["i_can_second"]
    assert api.post(f"/api/v1/motions/{mid}/second/").data["seconds"] == 1
    later = timezone.now() + timedelta(days=8)
    monkeypatch.setattr(timezone, "now", lambda: later)  # a week on, still one second
    assert api.get(f"/api/v1/chamas/{c.id}/motions/").data[0]["state"] == "expired"
    assert Motion.objects.get(pk=mid).vote_id is None


def test_a_poll_needs_two_to_six_different_options(seven):
    c, ms = seven
    with pytest.raises(Problem):
        votes.raise_motion(c, ms[0], kind="choice", question="Which one?", options=["A"])
    with pytest.raises(Problem):
        votes.raise_motion(c, ms[0], kind="choice", question="Which one?",
                           options=["A", "a"])
