"""Rotate the platform signing key without breaking phones' trust.

Run this while the server still signs with the OLD key. It signs the new key's
fingerprint with the old key and puts the new key on the chain. Then switch
PLATFORM_SIGNING_KEY_PEM (or KMS_KEY_ID) to the new key and restart.

    python manage.py endorse_platform_key --key-id <new key id> --public-pem new.pub.pem
"""

from django.core.management.base import BaseCommand

from apps.ledger.trust import endorse


class Command(BaseCommand):
    help = "Endorse a new platform signing key with the current one."

    def add_arguments(self, parser):
        parser.add_argument("--key-id", required=True)
        parser.add_argument("--public-pem", required=True, help="Path to the new public key")

    def handle(self, *args, **opts):
        pem = open(opts["public_pem"], encoding="ascii").read()
        k = endorse(new_key_id=opts["key_id"], new_public_key_pem=pem)
        self.stdout.write(f"Endorsed {k.key_id} (fingerprint {k.fingerprint}) after "
                          f"{k.prev_key_id}. Now switch the signer to it.")
