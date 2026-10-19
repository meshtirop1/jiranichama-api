"""A demo chama with a year of records, for trying the app on a phone or emulator.

Development only: refuses to run unless DEBUG is on. Sign in on the app with the demo
phone (default 0700 000 001); the code arrives on the dev SMS page (/dev/sms/).

    python manage.py seed_demo [--phone +254700000001] [--members 12] [--months 12]
"""

import random
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chamas import services as chamas
from apps.chamas.models import Chama, Membership
from apps.fines.models import Fine
from apps.ledger import services as ledger
from apps.rules import calendar
from apps.rules.models import RuleVersion, rules_hash
from apps.rules.templates import template_for

NAMES = ["Jane Wanjiru", "Peter Otieno", "Mary Achieng", "John Kamau", "Grace Njeri",
         "David Mutua", "Esther Chebet", "Samuel Kiprop", "Agnes Wambui", "Joseph Onyango",
         "Lucy Nyambura", "Daniel Kiplagat", "Ruth Moraa", "Paul Mwangi", "Faith Akinyi"]


class Command(BaseCommand):
    help = "Create a demo chama with a year of records (DEBUG only)."

    def add_arguments(self, parser):
        parser.add_argument("--phone", default="+254700000001")
        parser.add_argument("--members", type=int, default=12)
        parser.add_argument("--months", type=int, default=12)

    @transaction.atomic
    def handle(self, *args, phone, members, months, **opts):
        if not settings.DEBUG:
            raise CommandError("seed_demo runs only with DEBUG on.")
        rnd = random.Random(42)
        today = timezone.localdate()
        since = (today.replace(day=1) - timedelta(days=31 * months)).replace(day=1)
        founder = User.objects.filter(phone_hash=User.hash_for(phone)).first() \
            if hasattr(User, "hash_for") else None
        if founder is None:
            founder = User.objects.create_user(phone, display_name=NAMES[0])
        chama = chamas.create_chama(founder=founder, name=f"Demo Chama {rnd.randint(100, 999)}",
                                    type="table_banking", founder_role="chair")
        chamas.set_bank_account(chama, bank="equity", account_name="DEMO CHAMA",
                                account_no="0123456789012", statement_route="upload")
        ms = [Membership.objects.get(chama=chama, user=founder)]
        roles = ["treasurer", "secretary"] + ["member"] * (members - 3)
        for i, role in enumerate(roles, start=1):
            u = User.objects.create_user(f"+2547001{rnd.randint(10000, 99999)}",
                                         display_name=NAMES[i % len(NAMES)])
            locked = Chama.objects.select_for_update().get(pk=chama.pk)
            ms.append(Membership.objects.create(
                chama=chama, user=u, role=role, member_code=chamas._next_member_code(locked)))
        rules = template_for("table_banking")
        RuleVersion.objects.create(chama=chama, version_no=1, rules=rules, schema_version=1,
                                   template="table_banking@1", effective_from=since,
                                   hash=rules_hash(rules), state=RuleVersion.State.IN_FORCE)
        Membership.objects.filter(chama=chama).update(joined_on=since, is_founding=True)
        chama.refresh_from_db()
        chama.state, chama.activated_at = Chama.State.ACTIVE, timezone.now()
        chama.save()

        amount = rules["contributions"]["amount_cents"]
        fine = rules["fines"]["late_contribution"]["amount_cents"]
        entries = 0
        for p in calendar.periods(calendar.period_of(since), calendar.period_of(today)):
            due = calendar.due_date(p, rules)
            if due > today:
                break
            for m in ms:
                m.refresh_from_db()
                roll = rnd.random()
                if roll < 0.08:
                    continue  # missed this month
                late = roll < 0.25
                paid_on = due + timedelta(days=rnd.randint(4, 9) if late
                                          else -rnd.randint(0, 3))
                ledger.append(chama=chama, type="contribution", source="demo", member=m,
                              amount_cents=amount, period=p,
                              decision={"outcome": "late" if late else "on_time",
                                        "value_date": min(paid_on, today).isoformat()})
                entries += 1
                if late and fine:
                    e = ledger.append(chama=chama, type="fine_raised", source="demo",
                                      member=m, amount_cents=fine, period=p,
                                      rule_refs=["3.2"], decision={"cause": "late_contribution"})
                    Fine.objects.create(chama=chama, membership=m, rule_ref="3.2", period=p,
                                        amount_cents=fine, raised_entry=e)
                    entries += 1
        self.stdout.write(self.style.SUCCESS(
            f"{chama.name}: {len(ms)} members, {entries} entries. Sign in with {phone}."))
