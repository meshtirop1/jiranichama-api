"""Joining a chama from a basic phone: the invite by SMS, and YES <code> (5.2a)."""

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.phones import phone_hash
from apps.chamas import services as chamas
from apps.chamas.models import Membership
from apps.core.problems import Problem
from apps.ledger.models import LedgerEntry
from apps.notify.commands import handle

from .phase1 import active_chama, idem

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()
NEW = "+254799111222"


def _invite(api, chama, official, **extra):
    api.force_authenticate(official.user)
    return api.post(f"/api/v1/chamas/{chama.id}/invites/",
                    {"phone": "0799111222", "by_sms": True, **extra}, format="json", **idem())


def test_a_basic_phone_member_is_invited_and_joins_by_replying(
        api, sms_outbox, django_capture_on_commit_callbacks):
    c, (chair, *_) = active_chama(since=TODAY)
    with django_capture_on_commit_callbacks(execute=True):
        r = _invite(api, c, chair)
    assert r.status_code == 201, r.content
    code = r.json()["code"]
    to, text = sms_outbox[-1]
    assert to == NEW
    assert "invites you to join Test Chama" in text and f"Reply YES {code}" in text
    assert "KES 1,000 a month by day 5" in text

    reply = handle(NEW, f"yes {code.lower()}")
    assert reply.startswith("JiraniChama: welcome to Test Chama. Your member code is ")
    user = User.objects.get(phone_hash=phone_hash(NEW))
    m = Membership.objects.get(chama=c, user=user)
    assert m.role == "member" and m.joined_on == TODAY and not m.is_founding
    entry = LedgerEntry.objects.get(chama=c, type="membership", member=m)
    assert entry.decision["via"] == "sms"
    assert handle(NEW, "BAL").startswith("JiraniChama: Test Chama: saved KES 0")


def test_kiswahili_invite_and_welcome(api, sms_outbox, django_capture_on_commit_callbacks):
    c, (chair, *_) = active_chama(since=TODAY)
    with django_capture_on_commit_callbacks(execute=True):
        code = _invite(api, c, chair, language="sw").json()["code"]
    assert f"Jibu NDIYO {code}" in sms_outbox[-1][1]
    assert handle(NEW, f"NDIYO {code}").startswith("JiraniChama: karibu Test Chama")


def test_only_the_invited_number_can_use_the_code(api, sms_outbox,
                                                  django_capture_on_commit_callbacks):
    c, (chair, treasurer, _) = active_chama(since=TODAY)
    with django_capture_on_commit_callbacks(execute=True):
        code = _invite(api, c, chair).json()["code"]
    before = len(sms_outbox)
    assert handle("+254799333444", f"YES {code}") is None  # a stranger: no answer at all
    assert handle(treasurer.user.phone, f"YES {code}") is None  # a member, other number
    assert len(sms_outbox) == before
    assert not User.objects.filter(phone_hash=phone_hash("+254799333444")).exists()


def test_sms_invites_are_for_members_of_an_active_chama(api):
    c, (chair, *_) = active_chama(since=TODAY)
    r = _invite(api, c, chair, role="secretary")
    assert r.status_code == 400 and r.json()["code"] == "sms_invite_not_allowed"
    with pytest.raises(Problem):
        chamas.invite(c, invited_by=chair.user, by_sms=True)  # no phone


def test_replying_twice_does_not_join_twice(api, sms_outbox, django_capture_on_commit_callbacks):
    c, (chair, *_) = active_chama(since=TODAY)
    with django_capture_on_commit_callbacks(execute=True):
        code = _invite(api, c, chair).json()["code"]
    handle(NEW, f"YES {code}")
    assert handle(NEW, f"YES {code}") is None  # the invite is used
    assert Membership.objects.filter(chama=c, user__phone_hash=phone_hash(NEW)).count() == 1
