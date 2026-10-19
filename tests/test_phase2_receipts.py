"""Payment receipts by SMS for members who do not use the app (5.3 step 8)."""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.reconciliation import services as recon

from .phase1 import active_chama, credit

pytestmark = pytest.mark.django_db


def _pay(chama, member, cents=100_000, day=date(2026, 2, 3)):
    return recon.match_credit(credit(chama, amount_cents=cents, value_date=day,
                                     narration=f"MPESA {member.member_code}"))


def test_a_member_off_the_app_gets_a_receipt_by_sms(sms_outbox,
                                                    django_capture_on_commit_callbacks):
    c, (_, treasurer, _) = active_chama(since=date(2026, 1, 1))
    before = len(sms_outbox)
    with django_capture_on_commit_callbacks(execute=True):
        _pay(c, treasurer)
    [(to, text)] = sms_outbox[before:]
    assert to == treasurer.user.phone
    assert text.startswith(f"JiraniChama: KES 1,000 received for Jan from Treasurer "
                           f"({treasurer.member_code}). Test Chama. Ref ")
    assert "Check: VERIFY " in text


def test_app_users_see_it_in_the_app_instead(sms_outbox, django_capture_on_commit_callbacks):
    c, (_, treasurer, _) = active_chama(since=date(2026, 1, 1))
    User.objects.filter(pk=treasurer.user_id).update(last_app_at=timezone.now())
    before = len(sms_outbox)
    with django_capture_on_commit_callbacks(execute=True):
        _pay(c, treasurer)
    assert len(sms_outbox) == before

    # Away for longer than the setting: SMS again.
    User.objects.filter(pk=treasurer.user_id).update(
        last_app_at=timezone.now() - timedelta(days=31))
    with django_capture_on_commit_callbacks(execute=True):
        _pay(c, treasurer, day=date(2026, 3, 3))
    assert len(sms_outbox) == before + 1


def test_receipts_in_kiswahili(sms_outbox, django_capture_on_commit_callbacks):
    c, (_, treasurer, _) = active_chama(since=date(2026, 1, 1))
    User.objects.filter(pk=treasurer.user_id).update(language="sw")
    with django_capture_on_commit_callbacks(execute=True):
        _pay(c, treasurer)
    assert "zimepokelewa za Jan kutoka kwa" in sms_outbox[-1][1]
    assert "HAKIKI " in sms_outbox[-1][1]


def test_using_the_app_is_noted(api):
    c, (chair, *_) = active_chama(since=date(2026, 1, 1))
    from rest_framework_simplejwt.tokens import RefreshToken

    token = str(RefreshToken.for_user(chair.user).access_token)
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert api.get("/api/v1/chamas/").status_code == 200
    chair.user.refresh_from_db()
    assert chair.user.last_app_at is not None
