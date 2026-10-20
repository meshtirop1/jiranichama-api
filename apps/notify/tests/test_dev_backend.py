import pytest

from apps.notify.sms import DevFileBackend


def test_dev_backend_refuses_in_production(settings, tmp_path):
    settings.DEBUG = False
    settings.DEMO_SIGNIN_CODE = ""
    settings.DEV_SMS_FILE = str(tmp_path / "sms.log")
    with pytest.raises(RuntimeError):
        DevFileBackend()


def test_the_test_server_writes_messages_to_its_file(settings, tmp_path):
    settings.DEBUG = False
    settings.DEMO_SIGNIN_CODE = "123456"
    settings.DEV_SMS_FILE = str(tmp_path / "sms.log")
    assert DevFileBackend().send("+256772123456", "code 123").ok
    assert "+256772123456" in (tmp_path / "sms.log").read_text()


def test_dev_outbox_is_not_mounted_in_tests(api):
    # Tests and CI run with DEBUG off, like production.
    assert api.get("/dev/sms/").status_code == 404
