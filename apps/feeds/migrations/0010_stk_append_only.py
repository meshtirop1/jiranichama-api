from django.db import migrations

from apps.core.guards import append_only_except


class Migration(migrations.Migration):
    dependencies = [
        ("feeds", "0009_darajachannel_stk_passkey_enc_stkrequest"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only_except(
            "feeds_stk_request", ["state", "checkout_request_id", "result_code",
                                  "result_text", "receipt", "bank_txn_id"])),
    ]
