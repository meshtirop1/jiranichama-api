from rest_framework import serializers

from apps.core import countries

from .phones import InvalidPhone, normalise_phone

_WEAK_PINS = {"000000", "111111", "123456", "654321", "121212", "123123"}


class PhoneField(serializers.CharField):
    def to_internal_value(self, data):
        try:
            return normalise_phone(super().to_internal_value(data))
        except InvalidPhone as exc:
            raise serializers.ValidationError(str(exc)) from exc


_LANGUAGES = list(countries.LANGUAGES)
_OPEN = list(countries.OPEN)


class _CountryPhone(serializers.Serializer):
    """A phone number read in the chosen country (or in its own, when written with a
    country code). Only countries JiraniChama has opened in can sign in."""

    phone = serializers.CharField(max_length=32)
    country = serializers.ChoiceField(choices=_OPEN, default=countries.DEFAULT_COUNTRY)

    def validate(self, attrs):
        try:
            attrs["phone"] = normalise_phone(attrs["phone"], attrs.get("country"))
        except InvalidPhone as exc:
            raise serializers.ValidationError({"phone": [str(exc)]}) from exc
        home = countries.country_of_e164(attrs["phone"])
        if home is not None and not home.open:
            raise serializers.ValidationError(
                {"phone": [f"JiraniChama is not open in {home.name} yet."]})
        return attrs


class RequestCodeSerializer(_CountryPhone):
    language = serializers.ChoiceField(choices=countries.LANGUAGE_CHOICES, default="en")


class RequestCodeResponseSerializer(serializers.Serializer):
    phone_hint = serializers.CharField()
    expires_in = serializers.IntegerField()


class VerifyCodeSerializer(_CountryPhone):
    code = serializers.RegexField(r"^\d{6}$")


class SignInUserSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    phone_hint = serializers.CharField()
    has_pin = serializers.BooleanField()
    language = serializers.CharField()


class SignInResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    is_new = serializers.BooleanField()
    user = SignInUserSerializer()


class SetPinSerializer(serializers.Serializer):
    pin = serializers.RegexField(r"^\d{6}$")
    current_pin = serializers.RegexField(r"^\d{6}$", required=False)

    def validate_pin(self, value):
        if value in _WEAK_PINS:
            raise serializers.ValidationError("Choose a PIN that is harder to guess.")
        return value


class ProfileSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    phone_hint = serializers.CharField(read_only=True)
    display_name = serializers.CharField(min_length=2, max_length=60, trim_whitespace=True)
    language = serializers.ChoiceField(choices=countries.LANGUAGE_CHOICES, required=False)
    country = serializers.ChoiceField(choices=_OPEN, required=False)
    has_pin = serializers.BooleanField(read_only=True)
    date_joined = serializers.DateTimeField(read_only=True)
    # The ID check (Smile ID, paid by the chama) is not switched on yet: everyone is
    # "not_started" until it is. The app shows the status in Settings.
    kyc_status = serializers.SerializerMethodField()

    def get_kyc_status(self, user) -> str:
        from apps.identity.services import status_of

        return status_of(user)


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()
