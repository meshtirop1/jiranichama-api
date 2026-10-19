from django.db import migrations

from apps.core.guards import append_only_except
from apps.rules.models import RuleProposal


class Migration(migrations.Migration):
    dependencies = [
        ("rules", "0003_ruleproposal"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only_except("rules_rule_proposal", RuleProposal.MUTABLE)),
    ]
