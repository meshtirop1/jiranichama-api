"""Support access (5.16) and the staff admin console (6.11)."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import User
from apps.core.problems import Problem
from apps.flags.models import FeatureFlag
from apps.ledger.models import LedgerEntry
from apps.staff import support
from apps.staff.models import AuditLog

from .phase1 import active_chama

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


@pytest.fixture
def staff_client(settings):
    settings.SECURE_SSL_REDIRECT = False
    user = User.objects.create_staff("+254711999000", "pw-not-used", display_name="Ops")
    device = TOTPDevice.objects.create(user=user, name="phone", confirmed=True)
    client = Client()
    client.force_login(user)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()
    return client


def test_officials_give_confirm_and_revoke_support_access(sms_outbox,
                                                          django_capture_on_commit_callbacks):
    c, (chair, treasurer, secretary) = active_chama(since=TODAY)
    g = support.request(c, chair, areas=["ledger", "statements"], hours=48,
                        reason="Statement will not upload")
    with pytest.raises(Problem) as e:
        support.confirm(g, chair)
    assert e.value.problem_code == "second_official_required"
    with django_capture_on_commit_callbacks(execute=True):
        g = support.confirm(g, treasurer)
    assert g.state == "active" and g.ends_at > timezone.now() + timedelta(hours=47)
    assert "support read-only access" in sms_outbox[-1][1]
    assert LedgerEntry.objects.filter(type="support_access", source_id=g.id).exists()
    assert support.active_grant(c, "ledger") and support.active_grant(c, "members") is None
    support.revoke(g, secretary)
    assert support.active_grant(c) is None
    with pytest.raises(Problem):
        support.request(c, chair, areas=["everything"], reason="x")


def test_the_console_needs_staff_with_two_factor(api, settings):
    settings.SECURE_SSL_REDIRECT = False
    r = Client().get("/console/")
    assert r.status_code == 302 and "/staff/login/" in r["Location"]
    plain = User.objects.create_staff("+254711999001", "pw", display_name="No 2FA")
    client = Client()
    client.force_login(plain)  # signed in, but not verified with a code
    assert client.get("/console/").status_code == 302


def test_dashboard_and_queues_show_no_member_data(staff_client):
    c, _ = active_chama(since=TODAY)
    r = staff_client.get("/console/")
    assert r.status_code == 200 and b"Active chamas" in r.content
    for url in ("/console/queues/statements/", "/console/queues/integrity/",
                "/console/queues/support/", "/console/queues/referrals/", "/console/chamas/",
                "/console/flags/", "/console/feeds/", "/console/partners/", "/console/audit/"):
        assert staff_client.get(url).status_code == 200, url


def test_records_open_only_inside_a_grant_and_every_opening_is_logged(staff_client,
                                                                      sms_outbox):
    c, (chair, treasurer, secretary) = active_chama(since=TODAY)
    page = staff_client.get(f"/console/chamas/{c.id}/").content.decode()
    assert "has not given support access" in page and chair.member_code not in page
    assert staff_client.post(f"/console/chamas/{c.id}/",
                             {"reason": "x", "case_ref": "T-1"}).status_code == 404

    g = support.request(c, chair, areas=["members"], reason="Member list help")
    support.confirm(g, treasurer)
    page = staff_client.get(f"/console/chamas/{c.id}/").content.decode()
    assert "Open the records" in page and chair.member_code not in page
    r = staff_client.post(f"/console/chamas/{c.id}/",
                          {"reason": "Checking member list", "case_ref": "T-42"})
    assert r.status_code == 302
    page = staff_client.get(f"/console/chamas/{c.id}/").content.decode()
    assert chair.member_code in page and "Latest records" not in page  # members only
    a = AuditLog.objects.get(action="view:chama")
    assert a.case_ref == "T-42" and a.target == f"chamas.chama:{c.pk}"


def test_flags_change_only_with_a_reason_and_ticket(staff_client):
    c, _ = active_chama(since=TODAY)
    FeatureFlag.objects.create(key="loans")
    r = staff_client.post("/console/flags/loans/", {"add_prefix": c.member_code_prefix})
    assert r.status_code == 200  # reason and ticket missing
    assert FeatureFlag.objects.get(key="loans").chama_allowlist == []
    r = staff_client.post("/console/flags/loans/", {"add_prefix": c.member_code_prefix,
                                                    "reason": "Pilot", "case_ref": "T-7"})
    assert r.status_code == 302
    assert FeatureFlag.objects.get(key="loans").chama_allowlist == [c.id]
    assert AuditLog.objects.filter(action="change:flags.featureflag", case_ref="T-7").exists()


def test_support_access_through_the_api(api):
    c, (chair, treasurer, secretary) = active_chama(since=TODAY)
    api.force_authenticate(chair.user)
    r = api.post(f"/api/v1/chamas/{c.id}/support/",
                 {"areas": ["rules"], "hours": 24, "reason": "Rules question"}, format="json")
    assert r.status_code == 201, r.content
    gid = r.json()["id"]
    api.force_authenticate(treasurer.user)
    assert api.post(f"/api/v1/support-grants/{gid}/confirm/").json()["state"] == "active"
    api.force_authenticate(secretary.user)
    assert api.get(f"/api/v1/chamas/{c.id}/support/").json()[0]["state"] == "active"
