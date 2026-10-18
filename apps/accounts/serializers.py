from rest_framework import serializers

from .phones import InvalidPhone, normalise_phone

_WEAK_PINS = {"000000", "111111", "123456", "654321", "121212", "123123"}


class PhoneField(serializers.CharField):
    def to_internal_value(self, data):
        try:
            return normalise_phone(super().to_internal_value(data))
        except InvalidPhone as exc:
            raise serializers.ValidationError(str(exc)) from exc


class RequestCodeSerializer(serializers.Serializer):
    phone = PhoneField()
    language = serializers.ChoiceField(choices=["en", "sw"], default="en")


class RequestCodeResponseSerializer(serializers.Serializer):
    phone_hint = serializers.CharField()
    expires_in = serializers.IntegerField()


class VerifyCodeSerializer(serializers.Serializer):
    phone = PhoneField()
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


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()
