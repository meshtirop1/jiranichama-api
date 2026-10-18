"""Phase 1 end to end through the API: set up a chama, adopt its rules, bring in a bank
statement, match payments, and check the chain."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.chamas.models import Chama, Membership
from apps.feeds.models import BankTransaction
from apps.ledger import hashing
from apps.ledger.models import LedgerEntry
from apps.ledger.services import verify_chain

from .phase1 import idem, make_user, sms_code, statement_csv

pytestmark = pytest.mark.django_db


def _confirm_two_step(api, url, user, sms_outbox, expect=200):
    api.force_authenticate(user)
    r = api.post(url, {}, format="json")
    assert r.status_code == 202, r.content
    challenge = r.json()["challenge_id"]
    r = api.post(url, {"challenge_id": challenge, "code": sms_code(sms_outbox)}, format="json")
    assert r.status_code == expect, r.content
    return r.json()


@pytest.fixture
def people(db):
    return {
        "chair": make_user("+254711000001", "Jane"),
        "treasurer": make_user("+254711000002", "Peter"),
        "secretary": make_user("+254711000003", "Akinyi"),
        "member": make_user("+254711000004", "Wanjiru"),
    }


@pytest.fixture
def active(api, people, sms_outbox, settings):
    """Run the whole setup through the API and return the active chama."""
    api.force_authenticate(people["chair"])
    r = api.post("/api/v1/chamas/", {"name": "Umoja Women Group", "type": "merry_go_round",
                                     "founder_role": "chair", "expected_members": 4},
                 format="json", **idem())
    assert r.status_code == 201, r.content
    chama_id = r.json()["id"]
    assert r.json()["state"] == "setup" and r.json()["my_role"] == "chair"
    assert r.json()["i_am_founder"] is True
    assert r.json()["statement_inbox"].endswith("@" + settings.STATEMENT_INBOX_DOMAIN)

    r = api.put(f"/api/v1/chamas/{chama_id}/bank-account/",
                {"bank": "equity", "account_name": "UMOJA WOMEN GROUP",
                 "account_no": "0123 4567 89012", "statement_route": "upload"}, format="json")
    assert r.status_code == 200 and r.json()["account_no_last4"] == "9012"
    assert "account_no" not in r.json()

    rules = api.get(f"/api/v1/chamas/{chama_id}/rules/").json()
    assert rules["status"] == "draft"
    bad = {**rules["rules"], "approvals": {**rules["rules"]["approvals"], "tiers": [
        {"up_to_cents": None, "officials": 3, "member_vote": False}]}}
    r = api.put(f"/api/v1/chamas/{chama_id}/rules/", bad, format="json")
    assert r.status_code == 400 and r.json()["code"] == "rules_invalid"

    codes = {}
    for role, phone in [("treasurer", "0711000002"), ("secretary", "0711000003"),
                        ("member", "0711000004")]:
        r = api.post(f"/api/v1/chamas/{chama_id}/invites/", {"role": role, "phone": phone},
                     format="json")
        assert r.status_code == 201, r.content
        codes[role] = r.json()["code"]

    r = api.post(f"/api/v1/chamas/{chama_id}/submit/", {}, format="json")
    assert r.json()["state"] == "awaiting_officials"

    # The wrong person cannot use an invite sent to someone else's phone.
    api.force_authenticate(people["member"])
    assert api.post(f"/api/v1/invites/{codes['treasurer']}/", {},
                    format="json").json()["code"] == "invite_other_phone"

    for role in ("treasurer", "secretary", "member"):
        joined = _confirm_two_step(api, f"/api/v1/invites/{codes[role]}/", people[role],
                                   sms_outbox, expect=201)
        assert joined["role"] == role
    assert "JOIN Umoja Women Group" in sms_outbox[-1][1]

    chama = Chama.objects.get(pk=chama_id)
    assert chama.state == Chama.State.AWAITING_ADOPTION
    assert not LedgerEntry.objects.filter(chama=chama).exists()

    for role in ("chair", "treasurer"):
        data = _confirm_two_step(api, f"/api/v1/chamas/{chama_id}/adopt/", people[role],
                                 sms_outbox)
        assert data["state"] == "awaiting_adoption"
    assert data["adoption"] == {"founding": 4, "confirmed": 2, "needed": 3,
                                "officials_waiting": [data["adoption"]["officials_waiting"][0]]}
    data = _confirm_two_step(api, f"/api/v1/chamas/{chama_id}/adopt/", people["secretary"],
                             sms_outbox)
    assert data["state"] == "active"
    return Chama.objects.get(pk=chama_id)


def test_setup_ends_active_with_rules_and_members_on_the_ledger(api, active, people):
    entries = list(LedgerEntry.objects.filter(chama=active).order_by("seq"))
    assert [e.type for e in entries] == ["rule_version_adopted"] + ["membership"] * 4
    assert active.mode == Chama.Mode.RECORDS_ONLY

    api.force_authenticate(people["member"])
    rules = api.get(f"/api/v1/chamas/{active.id}/rules/").json()
    assert rules["status"] == "in_force" and rules["version_no"] == 1
    assert [c["ref"] for c in rules["book"]["en"]] == [c["ref"] for c in rules["book"]["sw"]]
    # Changing the rules after adoption is not a setup edit.
    api.force_authenticate(people["chair"])
    r = api.put(f"/api/v1/chamas/{active.id}/rules/", rules["rules"], format="json")
    assert r.status_code == 409


def _upload(api, chama, user, data, name="march.csv"):
    api.force_authenticate(user)
    return api.post(f"/api/v1/chamas/{chama.id}/statements/",
                    {"file": SimpleUploadedFile(name, data, content_type="text/csv")},
                    format="multipart", **idem())


def test_statement_upload_confirm_match_assign_and_not_me(api, active, people, sms_outbox):
    today = timezone.localdate()
    treasurer_code = Membership.objects.get(chama=active, role="treasurer").member_code
    member = Membership.objects.get(chama=active, role="member")
    data = statement_csv([
        (today, f"MPESA C2B {treasurer_code} PETER", 0, 100_000),
        (today, "MPESA DEPOSIT 0711000004 WANJIRU", 0, 100_000),
        (today, "CASH DEPOSIT", 0, 30_000),
        (today, "LEDGER FEE", 1_500, 0),
    ], opening_cents=500_000)

    r = _upload(api, active, people["treasurer"], data)
    assert r.status_code == 403 and r.json()["code"] == "treasurer_cannot_upload"

    r = _upload(api, active, people["secretary"], data)
    assert r.status_code == 201, r.content
    statement = r.json()
    assert statement["state"] == "awaiting_confirmation" and statement["lines"] == 4
    assert statement["problems"] == []

    api.force_authenticate(people["secretary"])
    r = api.post(f"/api/v1/statements/{statement['id']}/confirm/", {}, format="json")
    assert r.json()["code"] == "second_official_required"

    data_after = _confirm_two_step(api, f"/api/v1/statements/{statement['id']}/confirm/",
                                   people["chair"], sms_outbox)
    assert data_after["state"] == "accepted" and data_after["confirmed_by"] == "Jane"
    assert data_after["uploaded_by"] == "Akinyi"

    txns = BankTransaction.objects.filter(chama=active)
    assert sorted(txns.values_list("state", flat=True)) == [
        "debit_pending", "recorded", "recorded", "unmatched"]
    by_member = {e.member.role: e for e in LedgerEntry.objects.filter(
        chama=active, type="contribution").select_related("member")}
    assert set(by_member) == {"treasurer", "member"}
    assert by_member["member"].decision["method"] == "phone"
    assert by_member["treasurer"].decision["method"] == "member_code"

    # The same file again is refused.
    assert _upload(api, active, people["secretary"], data).json()["code"] == \
        "statement_duplicate"

    # The treasurer assigns the cash deposit; only the treasurer may.
    unmatched = api.get(f"/api/v1/chamas/{active.id}/unmatched/").json()
    assert len(unmatched) == 1 and unmatched[0]["narration"] == "CASH DEPOSIT"
    txn_id = unmatched[0]["id"]
    api.force_authenticate(people["chair"])
    r = api.post(f"/api/v1/bank-transactions/{txn_id}/assign/",
                 {"membership_id": str(member.id), "reason": "x"}, format="json", **idem())
    assert r.status_code == 403
    api.force_authenticate(people["treasurer"])
    key = idem()
    body = {"membership_id": str(member.id), "reason": "paid from her husband's phone"}
    r = api.post(f"/api/v1/bank-transactions/{txn_id}/assign/", body, format="json", **key)
    assert r.status_code == 200 and r.json()["state"] == "recorded"
    replay = api.post(f"/api/v1/bank-transactions/{txn_id}/assign/", body, format="json", **key)
    assert replay.status_code == 200 and replay["Idempotent-Replayed"] == "true"

    position = api.get(f"/api/v1/memberships/{member.id}/position/").json()
    assert position["contributed_cents"] + position["credit_cents"] == 130_000

    # The member says it was not theirs: everything it produced is reversed.
    api.force_authenticate(people["member"])
    r = api.post(f"/api/v1/bank-transactions/{txn_id}/not-me/", {"reason": "not mine"},
                 format="json", **idem())
    assert r.status_code == 200 and r.json()["state"] == "unmatched"
    position = api.get(f"/api/v1/memberships/{member.id}/position/").json()
    assert position["contributed_cents"] + position["credit_cents"] == 100_000
    assert LedgerEntry.objects.filter(chama=active, type="reversal").exists()

    # It can be assigned again and sent back again without double reversals.
    api.force_authenticate(people["treasurer"])
    api.post(f"/api/v1/bank-transactions/{txn_id}/assign/", body, format="json", **idem())
    api.force_authenticate(people["member"])
    api.post(f"/api/v1/bank-transactions/{txn_id}/not-me/", {}, format="json", **idem())
    position = api.get(f"/api/v1/memberships/{member.id}/position/").json()
    assert position["contributed_cents"] + position["credit_cents"] == 100_000

    for entry in LedgerEntry.objects.filter(chama=active):
        from apps.ledger.services import sign_entry
        sign_entry(entry.id)
    report = verify_chain(active)
    assert report.ok, report.problems


def test_statements_that_fail_checks_are_rejected(api, active, people):
    today = timezone.localdate()
    wrong_account = statement_csv([(today, "X", 0, 1_000)], opening_cents=0,
                                  account_no="9999999999")
    r = _upload(api, active, people["secretary"], wrong_account)
    assert r.json()["state"] == "rejected"
    assert [p["code"] for p in r.json()["problems"]] == ["account_mismatch"]

    tampered = statement_csv([(today, "X", 0, 1_000)], opening_cents=0).replace(
        b"Closing Balance:,10.00", b"Closing Balance:,99.00")
    r = _upload(api, active, people["secretary"], tampered)
    assert "balances_do_not_add_up" in [p["code"] for p in r.json()["problems"]]

    r = _upload(api, active, people["secretary"], b"%PDF-1.7", name="march.pdf")
    assert r.json()["state"] == "needs_parser"
    assert not BankTransaction.objects.filter(chama=active).exists()


def test_phone_reads_and_rechecks_the_chain(api, active, people):
    api.force_authenticate(people["member"])
    body = api.get(f"/api/v1/chamas/{active.id}/ledger/").json()
    prev = hashing.GENESIS_PREV_HASH
    for e in body["entries"]:
        assert e["document"]["prev_hash"] == prev
        assert hashing.entry_hash(e["document"]) == e["hash"]
        prev = e["hash"]
    head = api.get(f"/api/v1/chamas/{active.id}/ledger/head/").json()
    assert head["hash"] == prev and head["seq"] == len(body["entries"])
    key = api.get("/api/v1/platform-key/").json()
    assert key["public_key_pem"].startswith("-----BEGIN PUBLIC KEY-----")

    outsider = make_user("+254799999999", "Outsider")
    api.force_authenticate(outsider)
    assert api.get(f"/api/v1/chamas/{active.id}/ledger/").status_code == 404


def test_create_without_idempotency_key_is_refused(api, people):
    api.force_authenticate(people["chair"])
    r = api.post("/api/v1/chamas/", {"name": "X", "type": "welfare", "founder_role": "chair"},
                 format="json")
    assert r.status_code == 428
