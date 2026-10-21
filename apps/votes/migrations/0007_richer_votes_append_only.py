from django.db import migrations

from apps.core.guards import append_only, append_only_except

# The vote's trigger lists the columns that may change. Databases made before this change
# have the old list, so it is made again with the new tally columns (an explicit list: a
# later change to Vote.MUTABLE must not silently alter this migration).
OLD = ["state", "yes", "no", "result_entry_id"]
NEW = ["state", "yes", "no", "abstain", "option_counts", "winner", "result_entry_id"]


class Migration(migrations.Migration):
    dependencies = [
        ("votes", "0006_motion_motionsecond_votecomment_and_more"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[append_only_except("votes_vote", OLD)[1],
                 append_only_except("votes_vote", NEW)[0]],
            reverse_sql=[append_only_except("votes_vote", NEW)[1],
                         append_only_except("votes_vote", OLD)[0]]),
        migrations.RunSQL(*append_only("votes_comment")),
        migrations.RunSQL(*append_only_except("votes_motion", ["state", "vote_id"])),
        migrations.RunSQL(*append_only("votes_motion_second")),
    ]
