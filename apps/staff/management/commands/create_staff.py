import secrets

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import User
from apps.accounts.phones import InvalidPhone, normalise_phone, phone_hash
from apps.staff import audit


class Command(BaseCommand):
    help = ("Create a staff account with a TOTP device. Prints a one-time password and the "
            "otpauth:// link to add to an authenticator app.")

    def add_arguments(self, parser):
        parser.add_argument("--phone", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--reason", required=True)
        parser.add_argument("--case-ref", required=True)
        parser.add_argument("--superuser", action="store_true")

    def handle(self, *args, **opts):
        try:
            phone = normalise_phone(opts["phone"])
        except InvalidPhone as exc:
            raise CommandError(str(exc)) from exc
        if User.objects.filter(phone_hash=phone_hash(phone)).exists():
            raise CommandError("a user with this phone already exists")

        password = secrets.token_urlsafe(18)
        with transaction.atomic():
            user = User.objects.create_staff(phone, password, display_name=opts["name"],
                                             is_superuser=opts["superuser"])
            device = TOTPDevice.objects.create(user=user, name="authenticator", confirmed=True)
            audit.record(actor=None, action="create_staff", target=f"accounts.user:{user.pk}",
                         reason=opts["reason"], case_ref=opts["case_ref"])
        self.stdout.write(f"Staff account created for {user.display_name}.")
        self.stdout.write(f"One-time password (change it after first sign-in): {password}")
        self.stdout.write(f"Authenticator link: {device.config_url}")
