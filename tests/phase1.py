"""Helpers for Phase 1 tests."""

import re
import uuid
from datetime import date

from django.utils import timezone

from apps.accounts.models import User
from apps.chamas import services as chamas
from apps.chamas.models import Chama, Membership
from apps.feeds.models import BankTransaction
from apps.rules.models import RuleVersion, rules_hash
from apps.rules.templates import template_for

ACCOUNT_NO = "0123456789012"


def sms_code(outbox, phone_hint_tail: str | None = None) -> str:
    to, text = outbox[-1]
    if phone_hint_tail:
        assert to.endswith(phone_hint_tail)
    return re.search(r"\b(\d{6})\b", text).group(1)


def idem() -> dict:
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


def make_user(phone: str, name: str) -> User:
    return User.objects.create_user(phone, display_name=name)


def active_chama(*, since: date, members: int = 3, rules: dict | None = None,
                 bank: str = "equity") -> tuple[Chama, list[Membership]]:
    """An active chama whose rules started on ``since``, built without the SMS steps."""
    founder = make_user(f"+2547100{uuid.uuid4().int % 10**5:05d}", "Founder")
    chama = chamas.create_chama(founder=founder, name="Test Chama", type="merry_go_round",
                                founder_role="chair")
    chamas.set_bank_account(chama, bank=bank, account_name="TEST", account_no=ACCOUNT_NO,
                            statement_route="upload")
    roles = ["treasurer", "secretary"] + ["member"] * max(members - 3, 0)
    ms = [Membership.objects.get(chama=chama, user=founder)]
    for role in roles[: members - 1]:
        u = make_user(f"+2547110{uuid.uuid4().int % 10**5:05d}", role.title())
        chama.refresh_from_db()
        locked = Chama.objects.select_for_update().get(pk=chama.pk)
        ms.append(Membership.objects.create(chama=chama, user=u, role=role,
                                            member_code=chamas._next_member_code(locked)))
    r = rules or template_for("merry_go_round")
    RuleVersion.objects.create(chama=chama, version_no=1, rules=r, schema_version=1,
                               template="merry_go_round@1", effective_from=since,
                               hash=rules_hash(r), state=RuleVersion.State.IN_FORCE)
    Membership.objects.filter(chama=chama).update(joined_on=since, is_founding=True)
    chama.state = Chama.State.ACTIVE
    chama.activated_at = timezone.now()
    chama.save()
    for m in ms:
        m.refresh_from_db()
    return chama, ms


def credit(chama: Chama, *, amount_cents: int, value_date: date, narration: str = "",
           ref: str | None = None) -> BankTransaction:
    account = chamas.active_bank_account(chama)
    txn = BankTransaction(chama=chama, bank_account=account, source="statement",
                          external_ref=ref or uuid.uuid4().hex, direction="credit",
                          amount_cents=amount_cents, value_date=value_date,
                          state=BankTransaction.State.UNMATCHED)
    txn.set_narration(narration)
    txn.save()
    return txn


def statement_csv(lines: list[tuple], *, opening_cents: int, account_no: str = ACCOUNT_NO
                  ) -> bytes:
    """lines: (date, narration, debit_cents, credit_cents)."""
    def money(c):
        return "" if not c else f"{c / 100:.2f}"

    balance = opening_cents
    rows = []
    for d, narration, debit, credit_cents in lines:
        balance += credit_cents - debit
        rows.append(f"{d.strftime('%d/%m/%Y')},{narration},,{money(debit)},"
                    f"{money(credit_cents)},{balance / 100:.2f}")
    head = (f"Account Number:,{account_no}\nOpening Balance:,{opening_cents / 100:.2f}\n"
            f"Closing Balance:,{balance / 100:.2f}\n\n"
            "Value Date,Narration,Reference,Debit,Credit,Balance\n")
    return (head + "\n".join(rows) + "\n").encode()
