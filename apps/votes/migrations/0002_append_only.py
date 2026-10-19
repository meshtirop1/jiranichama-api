from django.db import migrations

from apps.core.guards import append_only, append_only_except
from apps.votes.models import Vote


class Migration(migrations.Migration):
    dependencies = [
        ("votes", "0001_initial"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only_except("votes_vote", Vote.MUTABLE)),
        migrations.RunSQL(*append_only("votes_ballot")),
        migrations.RunSQL(*append_only("votes_turnout")),
    ]
