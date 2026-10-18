import pytest

from apps.notify.sms import DevFileBackend


def test_dev_backend_refuses_outside_debug(settings, tmp_path):
    settings.DEBUG = False
    settings.DEV_SMS_FILE = str(tmp_path / "sms.log")
    with pytest.raises(RuntimeError):
        DevFileBackend()


def test_dev_outbox_is_not_mounted_in_tests(api):
    # Tests and CI run with DEBUG off, like production.
    assert api.get("/dev/sms/").status_code == 404
