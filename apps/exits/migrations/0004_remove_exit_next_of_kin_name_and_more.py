"""Next of kin encrypted at rest (privacy, 2026-10-20). Existing rows are encrypted in
place; the plain columns are then dropped. The append-only trigger is paused only for the
copy, inside this migration's transaction."""

import json

from django.db import migrations, models


def encrypt_existing(apps, schema_editor):
    from apps.core.crypto import encrypt

    Exit = apps.get_model("exits", "Exit")
    with schema_editor.connection.cursor() as c:
        c.execute("ALTER TABLE exits_exit DISABLE TRIGGER exits_exit_append_only;")
    for e in Exit.objects.exclude(next_of_kin_name="", next_of_kin_phone=""):
        Exit.objects.filter(pk=e.pk).update(next_of_kin_enc=encrypt(json.dumps(
            {"name": e.next_of_kin_name, "phone": e.next_of_kin_phone})))
    with schema_editor.connection.cursor() as c:
        c.execute("ALTER TABLE exits_exit ENABLE TRIGGER exits_exit_append_only;")


class Migration(migrations.Migration):

    dependencies = [
        ('exits', '0003_death_confirmation'),
    ]

    operations = [
        migrations.AddField(
            model_name='exit',
            name='next_of_kin_enc',
            field=models.BinaryField(blank=True, null=True),
        ),
        migrations.RunPython(encrypt_existing, migrations.RunPython.noop),
        migrations.RemoveField(model_name='exit', name='next_of_kin_name'),
        migrations.RemoveField(model_name='exit', name='next_of_kin_phone'),
    ]
