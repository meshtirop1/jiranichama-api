from contextlib import contextmanager

import pytest
from django.db import DatabaseError, connection, transaction

from apps.core.models import OutboxEvent
from apps.core.tasks import publish_outbox
from apps.ledger import hashing, services
from apps.ledger.models import ChainHead, EntrySignature, LedgerEntry

pytestmark = pytest.mark.django_db


@contextmanager
def _triggers_bypassed():
    """Only a database superuser can do this; the application role never can."""
    with connection.cursor() as cur:
        cur.execute("SET LOCAL session_replication_role = replica")
        try:
            yield cur
        finally:
            cur.execute("SET LOCAL session_replication_role = origin")


def _three_entries(chama, membership):
    first = services.append(chama=chama, type="opening_balance", source="proposal",
                            member=membership, amount_cents=1_250_000)
    second = services.append(chama=chama, type="contribution", source="bank_txn",
                             member=membership, amount_cents=100_000, period="2026-03",
                             rule_refs=["3.1"], decision={"outcome": "on_time"})
    third = services.append(chama=chama, type="correction", source="proposal",
                            amount_cents=-100_000, reverses=second)
    return first, second, third


def test_append_links_entries_into_a_chain(chama, membership):
    first, second, third = _three_entries(chama, membership)

    assert [e.seq for e in (first, second, third)] == [1, 2, 3]
    assert first.prev_hash == hashing.GENESIS_PREV_HASH
    assert second.prev_hash == first.hash
    assert third.prev_hash == second.hash
    assert second.member_code == membership.member_code
    assert third.reverses_id == second.id
    assert first.receipt_code == hashing.receipt_code(first.hash)

    head = ChainHead.objects.get(chama=chama)
    assert (head.seq, head.hash) == (3, third.hash)

    stored = LedgerEntry.objects.get(pk=second.pk)
    assert hashing.entry_hash(services.document_for(stored)) == stored.hash


def test_append_writes_an_outbox_event_per_entry(chama, membership):
    _three_entries(chama, membership)
    assert OutboxEvent.objects.filter(topic="ledger.entry_appended").count() == 3


def test_outbox_publishing_signs_entries(chama, membership, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    _three_entries(chama, membership)
    assert publish_outbox() == 3
    assert EntrySignature.objects.count() == 3

    report = services.verify_chain(chama)
    assert report.ok, report.problems
    assert report.entries == 3 and report.unsigned == []


def test_sign_entry_is_idempotent(chama, membership):
    entry = services.append(chama=chama, type="contribution", source="bank_txn",
                            member=membership, amount_cents=5_000)
    a = services.sign_entry(entry.id)
    b = services.sign_entry(entry.id)
    assert a.pk == b.pk and EntrySignature.objects.count() == 1


def test_member_from_another_chama_is_refused(chama, membership, user):
    from apps.chamas.models import Chama

    other = Chama.objects.create(name="Other", type=Chama.Type.WELFARE,
                                 member_code_prefix="JC-OTHR", created_by=user)
    with pytest.raises(ValueError):
        services.append(chama=other, type="contribution", source="bank_txn", member=membership)


@pytest.mark.parametrize("sql", [
    "UPDATE ledger_entry SET amount_cents = 1",
    "DELETE FROM ledger_entry",
])
def test_database_refuses_changes_to_ledger(chama, membership, sql):
    services.append(chama=chama, type="contribution", source="bank_txn", member=membership,
                    amount_cents=100_000)
    with pytest.raises(DatabaseError, match="append-only"):
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(sql)


def test_signature_rows_are_append_only_too(chama, membership):
    entry = services.append(chama=chama, type="contribution", source="bank_txn",
                            member=membership, amount_cents=100_000)
    services.sign_entry(entry.id)
    with pytest.raises(DatabaseError, match="append-only"):
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("DELETE FROM ledger_entry_signature")


def test_tampering_is_found_at_the_right_position(chama, membership):
    _, second, _ = _three_entries(chama, membership)
    for entry in LedgerEntry.objects.filter(chama=chama):
        services.sign_entry(entry.id)

    # Simulate an insider with database-superuser rights who bypasses the triggers.
    with _triggers_bypassed() as cur:
        cur.execute("UPDATE ledger_entry SET amount_cents = 999999 WHERE id = %s", [second.id])

    report = services.verify_chain(chama)
    assert not report.ok
    assert report.problems == ["seq 2: contents do not match its hash"]


def test_forged_signature_is_reported(chama, membership, platform_key):
    entry = services.append(chama=chama, type="contribution", source="bank_txn",
                            member=membership, amount_cents=100_000)
    services.sign_entry(entry.id)
    with _triggers_bypassed() as cur:
        cur.execute("UPDATE ledger_entry_signature SET platform_sig = %s", [b"\x30\x00"])
    report = services.verify_chain(chama)
    assert report.problems == ["seq 1: platform signature is not valid"]
