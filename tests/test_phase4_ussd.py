"""The USSD menu and the LOAN command for basic phones (4.9)."""

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.chamas.models import Membership
from apps.flags.models import FeatureFlag
from apps.ledger import services as ledger
from apps.notify.commands import handle as sms
from apps.notify.models import UssdRequest
from apps.notify.ussd import SCREEN, handle
from apps.rules.templates import template_for

from .phase1 import active_chama, make_user

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def test_one_chama_goes_straight_to_the_menu_and_answers_read_only():
    c, (chair, *_) = active_chama(since=TODAY)
    phone = chair.user.phone
    menu = handle(phone, "")
    assert menu.startswith("CON Test Chama\n1. Balance") and "6. Verify a receipt" in menu
    assert handle(phone, "1").startswith("END Test Chama: saved KES 0")
    assert handle(phone, "5").startswith("END Test Chama: KES 1,000 a month by day 5")
    assert handle(phone, "3") == "END Loans are not offered."
    assert handle(phone, "6") == "CON Enter the receipt code:"
    e = ledger.append(chama=c, type="contribution", source="test", member=chair,
                      amount_cents=100_000, period="2026-01")
    assert handle(phone, f"6*{e.receipt_code}").startswith("END GENUINE: contribution")
    assert handle(phone, "8") == "END Choose a number from the menu."
    assert UssdRequest.objects.filter(user=chair.user).count() == 7


def test_several_chamas_choose_first_and_kiswahili_members_get_kiswahili():
    c1, (chair, *_) = active_chama(since=TODAY)
    c2, _ = active_chama(since=TODAY)
    Membership.objects.create(chama=c2, user=chair.user, joined_on=TODAY,
                              member_code=f"{c2.member_code_prefix}-009")
    User.objects.filter(pk=chair.user.pk).update(language="sw")
    first = handle(chair.user.phone, "")
    assert first.startswith("CON Chagua chama:\n1. Test Chama\n2. Test Chama")
    assert handle(chair.user.phone, "2").startswith("CON Test Chama\n1. Salio")
    assert handle(chair.user.phone, "2*1").startswith("END Test Chama: akiba")
    assert handle(chair.user.phone, "7").startswith("END Chagua nambari")


def test_strangers_and_floods_are_turned_away(settings):
    assert "not registered" in handle("+254799111222", "")
    settings.USSD_REQUESTS_PER_HOUR = 2
    u = make_user("+254799111333", "X")
    handle(u.phone, "")
    handle(u.phone, "1")
    assert "Too many requests" in handle(u.phone, "1")


def test_answers_fit_one_screen():
    c, (chair, *_) = active_chama(since=TODAY)
    c.name = "A very long chama name " * 5
    c.save(update_fields=["name"])
    assert len(handle(chair.user.phone, "5")) <= SCREEN + 4


def test_loan_by_sms_and_ussd_where_loans_are_on(sms_outbox):
    c, (chair, *_) = active_chama(since=TODAY, rules=template_for("table_banking"))
    FeatureFlag.objects.create(key="loans", chama_allowlist=[c.id])
    assert handle(chair.user.phone, "3") == "END Test Chama: no loan."
    sms(chair.user.phone, "MKOPO")
    assert sms_outbox[-1][1] == "JiraniChama: Test Chama: hakuna mkopo."


def test_the_hook_needs_its_token(api, settings):
    c, (chair, *_) = active_chama(since=TODAY)
    settings.USSD_TOKEN = "t0ken"
    assert api.post("/api/v1/hooks/ussd/wrong/", {}).status_code == 404
    r = api.post("/api/v1/hooks/ussd/t0ken/", {"sessionId": "s1", "serviceCode": "*384#",
                                                 "phoneNumber": chair.user.phone,
                                                 "text": ""})
    assert r.status_code == 200 and r.content.decode().startswith("CON Test Chama")
