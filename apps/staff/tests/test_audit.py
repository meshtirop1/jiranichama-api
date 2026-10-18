import pytest
from django.db import DatabaseError, connection, transaction

from apps.staff import audit
from apps.staff.models import AuditLog

pytestmark = pytest.mark.django_db


def test_records_chain_and_verify(user):
    a = audit.record(actor=user, action="change:flags.featureflag", target="flags:1",
                     reason="Pilot chama asked", case_ref="SUP-12")
    b = audit.record(actor=None, action="create_staff", reason="New hire", case_ref="HR-3")
    assert (a.seq, b.seq) == (1, 2)
    assert b.prev_hash == a.hash
    assert audit.verify_audit_chain() == []


@pytest.mark.parametrize("reason,case_ref", [("", "SUP-1"), ("why", ""), ("  ", "SUP-1")])
def test_reason_and_ticket_are_required(user, reason, case_ref):
    with pytest.raises(audit.MissingReason):
        audit.record(actor=user, action="x", reason=reason, case_ref=case_ref)
    assert AuditLog.objects.count() == 0


def test_audit_log_is_append_only(user):
    audit.record(actor=user, action="x", reason="r", case_ref="c")
    with pytest.raises(DatabaseError, match="append-only"):
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("UPDATE staff_audit_log SET reason = 'edited'")


def test_staff_console_requires_two_factor(client, settings):
    from apps.accounts.models import User

    staff = User.objects.create_staff("+254700000001", "a-long-staff-password-1")
    client.force_login(staff)
    r = client.get("/staff/", secure=True)
    # Signed in with a password but no verified OTP device: sent back to the login page.
    assert r.status_code == 302 and "/staff/login/" in r["Location"]
