import uuid

import pytest

from apps.flags.models import FeatureFlag, is_enabled

pytestmark = pytest.mark.django_db


def test_unknown_flag_is_off():
    assert is_enabled("loans") is False


def test_allowlist_scopes_flag_to_pilot_chamas():
    pilot, other = uuid.uuid4(), uuid.uuid4()
    FeatureFlag.objects.create(key="loans", chama_allowlist=[pilot])
    assert is_enabled("loans", pilot) is True
    assert is_enabled("loans", other) is False
    assert is_enabled("loans") is False


def test_enabled_for_all():
    FeatureFlag.objects.create(key="statements", enabled_for_all=True)
    assert is_enabled("statements", uuid.uuid4()) is True
