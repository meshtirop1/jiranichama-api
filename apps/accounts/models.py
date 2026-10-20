import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core import countries
from apps.core.crypto import decrypt, encrypt

from .phones import phone_hash, phone_hint


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_by_phone(self, e164: str):
        return self.get(phone_hash=phone_hash(e164))

    def create_user(self, phone: str, **extra):
        user = self.model(**extra)
        user.set_phone(phone)
        home = countries.country_of_e164(phone)
        if home is not None and "country" not in extra:
            user.country = home.code  # a new person's country is their phone's
        user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_staff(self, phone: str, password: str, **extra):
        user = self.model(is_staff=True, **extra)
        user.set_phone(phone)
        user.set_password(password)
        user.save(using=self._db)
        return user


class User(AbstractBaseUser, PermissionsMixin):
    """A person. Identified by phone; the phone is stored encrypted and looked up by a
    keyed hash, so it never sits in the database in plain text.

    User rows are never deleted: history tables point at them with PROTECT. Deleting an
    account calls ``anonymise()`` instead (6.1 principle 9).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone_hash = models.CharField(max_length=80, unique=True)
    phone_enc = models.BinaryField(editable=False)
    phone_hint = models.CharField(max_length=24, blank=True, default="")
    display_name = models.CharField(max_length=120, blank=True, default="")
    pin_hash = models.CharField(max_length=128, blank=True, default="")
    language = models.CharField(max_length=3, default="en",
                                choices=list(countries.LANGUAGES.items()))
    # Chosen at sign-up; sets how local phone numbers are read and new chamas' currency.
    country = models.CharField(max_length=2, default=countries.DEFAULT_COUNTRY,
                               choices=[(c.code, c.name) for c in countries.COUNTRIES.values()])
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    anonymised_at = models.DateTimeField(null=True, blank=True)
    # When they last used the app (at most hourly); members away get SMS receipts.
    last_app_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "phone_hash"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "accounts_user"

    def __str__(self):
        return self.display_name or self.phone_hint or str(self.id)

    @property
    def phone(self) -> str:
        return decrypt(self.phone_enc) if self.phone_enc else ""

    def set_phone(self, e164: str) -> None:
        self.phone_enc = encrypt(e164)
        self.phone_hash = phone_hash(e164)
        self.phone_hint = phone_hint(e164)

    @property
    def has_pin(self) -> bool:
        return bool(self.pin_hash)

    def set_pin(self, pin: str) -> None:
        self.pin_hash = make_password(pin)

    def check_pin(self, pin: str) -> bool:
        return bool(self.pin_hash) and check_password(pin, self.pin_hash)

    def anonymise(self) -> None:
        """Remove personal details in place; history keeps only member codes."""
        self.phone_enc = b""
        self.phone_hash = f"anonymised:{self.id}"
        self.phone_hint = ""
        self.display_name = ""
        self.pin_hash = ""
        self.is_active = False
        self.set_unusable_password()
        self.anonymised_at = timezone.now()
        self.save()


class OtpChallenge(models.Model):
    """A one-time sign-in code. Only a keyed hash of the code is stored."""

    PURPOSE_SIGN_IN = "sign_in"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone_hash = models.CharField(max_length=80, db_index=True)
    purpose = models.CharField(max_length=32, default=PURPOSE_SIGN_IN)
    code_hmac = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_otp_challenge"
        indexes = [models.Index(fields=["phone_hash", "created_at"])]
