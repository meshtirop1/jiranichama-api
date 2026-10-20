"""ID checks through Smile ID (off until configured), one person one ID, rule P.3, and the
privacy fixes of 2026-10-20."""

from unittest import mock

import pytest
from django.db import connection
from django.utils import timezone

from apps.core.problems import Problem
from apps.identity import services as kyc
from apps.identity.models import IdentityCheck
from apps.payouts import services as payouts

from .phase1 import active_chama, make_user

pytestmark = pytest.mark.django_db
TODAY = timezone.localdate()


@pytest.fixture
def smile(settings):
    settings.SMILE_ID_PARTNER_ID = "1234"
    settings.SMILE_ID_API_KEY = "test-key"
    return settings


def _answer(code="1012", name="KAMAU WANJIRU"):
    resp = mock.Mock()
    resp.raise_for_status = lambda: None
    resp.json = lambda: {"ResultCode": code, "FullName": name}
    return mock.patch("apps.identity.smile.requests.post", return_value=resp)


def test_checks_are_off_until_smile_id_is_configured(api):
    u = make_user("+254712000101", "Wanjiru Kamau")
    api.force_authenticate(u)
    me = api.get("/api/v1/me/identity/").json()
    assert me["available"] is False and me["status"] == "not_started"
    r = api.post("/api/v1/me/identity/", {"country": "KE", "id_type": "NATIONAL_ID",
                                          "id_number": "12345678", "first_name": "Wanjiru",
                                          "last_name": "Kamau"}, format="json")
    assert r.status_code == 503


def test_a_member_checks_their_id_and_others_see_only_a_tick(api, smile):
    c, (chair, treasurer, secretary) = active_chama(since=TODAY)
    chair.user.display_name = "Wanjiru Kamau"
    chair.user.save()
    api.force_authenticate(chair.user)
    with _answer() as post:
        r = api.post("/api/v1/me/identity/", {
            "country": "KE", "id_type": "NATIONAL_ID", "id_number": "1234 5678",
            "first_name": "Wanjiru", "last_name": "Kamau", "paid_by": str(c.id)},
            format="json")
    assert r.status_code == 201 and r.json()["status"] == "verified"
    sent = post.call_args.kwargs["json"]
    assert sent["partner_id"] == "1234" and sent["partner_params"]["job_type"] == 5
    check = IdentityCheck.objects.get(user=chair.user)
    assert check.paid_by == c and check.last4 == "5678"
    # The number itself is not stored anywhere.
    with connection.cursor() as cur:
        cur.execute("SELECT * FROM identity_check")
        assert "12345678" not in str(cur.fetchall())
    assert api.get("/api/v1/auth/me/").json()["kyc_status"] == "verified"
    members = api.get(f"/api/v1/chamas/{c.id}/members/").json()
    assert [m["id_checked"] for m in members] == [True, False, False]


def test_one_id_belongs_to_one_person(smile):
    a = make_user("+254712000102", "Wanjiru Kamau")
    b = make_user("+254712000103", "Wanjiru Kamau")
    with _answer():
        kyc.start(a, country="KE", id_type="NATIONAL_ID", id_number="12345678",
                  first_name="Wanjiru", last_name="Kamau")
    with pytest.raises(Problem) as exc, _answer():
        kyc.start(b, country="KE", id_type="NATIONAL_ID", id_number="12-345-678",
                  first_name="Wanjiru", last_name="Kamau")
    assert exc.value.problem_code == "id_already_used"


def test_a_valid_id_with_someone_elses_name_does_not_pass(smile):
    u = make_user("+254712000104", "Peter Otieno")
    with _answer(name="KAMAU WANJIRU"):
        c = kyc.start(u, country="KE", id_type="NATIONAL_ID", id_number="87654321",
                      first_name="Peter", last_name="Otieno")
    assert c.status == "failed" and not c.name_matches


def test_rule_p3_officials_pass_before_money_out(settings, smile):
    settings.KYC_ENFORCED = True
    c, (chair, treasurer, secretary) = active_chama(since=TODAY)
    with pytest.raises(Problem) as exc:
        payouts.start_enable(c, chair)
    assert exc.value.problem_code == "officials_not_verified"


def test_names_match_in_any_order():
    assert kyc.names_match("Wanjiru Kamau", "KAMAU WANJIRU MARY")
    assert not kyc.names_match("Wanjiru Kamau", "Peter Otieno")


def test_next_of_kin_is_encrypted_at_rest(sms_outbox):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.exits import services as exits
    from apps.exits.models import Exit

    c, (chair, treasurer, secretary, mary, john) = active_chama(since=TODAY, members=5)
    e = exits.record_death(c, secretary, mary, died_on=TODAY, next_of_kin_name="Paul Wanjiru",
                           next_of_kin_phone="0712999888",
                           document=SimpleUploadedFile("p.jpg", b"\xff\xd8 x",
                                                       content_type="image/jpeg"))
    e = Exit.objects.get(pk=e.pk)
    assert e.next_of_kin == ("Paul Wanjiru", "0712999888")
    with connection.cursor() as cur:
        cur.execute("SELECT next_of_kin_enc FROM exits_exit WHERE id = %s", [e.pk])
        raw = bytes(cur.fetchone()[0])
    assert b"0712999888" not in raw and b"Paul" not in raw


def test_vote_sms_carries_only_the_last_digits_of_the_payee(settings, sms_outbox):
    from apps.votes import services as votes

    from .test_phase2_votes import _setup

    c, chair, treasurer, secretary, mary, john, p = _setup(settings, sms_outbox)
    v = votes.for_subject(f"payouts.proposal:{p.id}")
    if p.payee_account:
        assert p.payee_account not in v.question
        assert p.payee_account[-4:] in v.question


def test_staff_see_a_chamas_records_only_with_a_support_grant(rf):
    from apps.ledger import services as ledger
    from apps.staff.admin import LedgerEntryAdmin
    from apps.staff.models import SupportGrant
    from apps.staff.site import staff_site

    c, ms = active_chama(since=TODAY)
    ledger.append(chama=c, type="membership", source="test", member=ms[0],
                  decision={"change": "joined"})
    from apps.ledger.models import LedgerEntry

    admin = LedgerEntryAdmin(LedgerEntry, staff_site)
    request = rf.get("/")
    assert not admin.get_queryset(request).filter(chama=c).exists()
    SupportGrant.objects.create(chama=c, areas=["ledger"], hours=24, reason="help",
                                requested_by=ms[0], confirmed_by=ms[1],
                                state=SupportGrant.State.ACTIVE, starts_at=timezone.now(),
                                ends_at=timezone.now() + timezone.timedelta(hours=24))
    assert admin.get_queryset(request).filter(chama=c).exists()
