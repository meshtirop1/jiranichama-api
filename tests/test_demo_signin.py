"""The fixed demo code on test servers: only demo phones, never by default."""

import pytest
from django.core import checks

from apps.accounts import services
from apps.core.problems import Problem
from apps.notify.sms import ConsoleBackend

pytestmark = pytest.mark.django_db


def test_off_by_default_every_phone_gets_an_sms(sms_outbox):
    services.request_code("+254700000001")
    assert len(ConsoleBackend.outbox) == 1
    assert not [m for m in checks.run_checks() if m.id == "jirani.W001"]


def test_demo_phones_use_the_fixed_code_and_get_no_sms(settings, sms_outbox):
    settings.DEMO_SIGNIN_CODE = "246810"
    settings.DEMO_PHONES = ["+254700000001"]
    services.request_code("+254700000001")
    assert ConsoleBackend.outbox == []
    result = services.verify_code("+254700000001", "246810")
    assert result.user is not None
    services.request_code("+254700000002")  # not a demo phone: a real code by SMS
    assert len(ConsoleBackend.outbox) == 1
    with pytest.raises(Problem):
        services.verify_code("+254700000002", "246810")
    assert [m for m in checks.run_checks() if m.id == "jirani.W001"]
