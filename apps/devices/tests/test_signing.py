import re
import uuid

import pytest

from apps.core.problems import Problem
from apps.devices import signing
from apps.devices.models import SigningChallenge

pytestmark = pytest.mark.django_db

PURPOSE = SigningChallenge.Purpose.ADOPT_RULES


def _issue(user, content="v1"):
    return signing.issue(user=user, purpose=PURPOSE, subject="chamas.chama:x",
                         content_hash=content, action_text="CONFIRM the rule book of Umoja")


def _code(sms_outbox):
    return re.search(r"\b(\d{6})\b", sms_outbox[-1][1]).group(1)


def _confirm(user, challenge, code, content="v1"):
    return signing.confirm(user=user, challenge_id=challenge.id, code=code, purpose=PURPOSE,
                           subject="chamas.chama:x", content_hash=content)


def test_sms_names_the_action(user, sms_outbox):
    _issue(user)
    assert "to CONFIRM the rule book of Umoja" in sms_outbox[-1][1]


def test_right_code_works_once(user, sms_outbox):
    c = _issue(user)
    code = _code(sms_outbox)
    _confirm(user, c, code)
    with pytest.raises(Problem) as exc:
        _confirm(user, c, code)
    assert exc.value.problem_code == "signing_expired"


def test_wrong_tries_are_counted_and_lock(user, sms_outbox):
    c = _issue(user)
    for left in (4, 3, 2, 1, 0):
        with pytest.raises(Problem) as exc:
            _confirm(user, c, "000000")
        assert exc.value.extra["attempts_left"] == left
    with pytest.raises(Problem) as exc:
        _confirm(user, c, _code(sms_outbox))
    assert exc.value.problem_code == "signing_locked"


def test_code_cannot_approve_changed_content(user, sms_outbox):
    c = _issue(user, content="what-they-saw")
    with pytest.raises(Problem) as exc:
        _confirm(user, c, _code(sms_outbox), content="something-else")
    assert exc.value.problem_code == "signing_content_changed"


def test_code_belongs_to_one_person(user, sms_outbox):
    from apps.accounts.models import User

    other = User.objects.create_user("+254722222222")
    c = _issue(user)
    with pytest.raises(Problem) as exc:
        _confirm(other, c, _code(sms_outbox))
    assert exc.value.problem_code == "signing_invalid"


def test_idempotency_key_cannot_be_reused_for_a_different_request(api, user):
    api.force_authenticate(user)
    key = {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}
    body = {"name": "Umoja", "type": "welfare", "founder_role": "chair"}
    assert api.post("/api/v1/chamas/", body, format="json", **key).status_code == 201
    replay = api.post("/api/v1/chamas/", body, format="json", **key)
    assert replay.status_code == 201 and replay["Idempotent-Replayed"] == "true"
    r = api.post("/api/v1/chamas/", {**body, "name": "Other"}, format="json", **key)
    assert r.status_code == 422 and r.json()["code"] == "idempotency_key_reused"
