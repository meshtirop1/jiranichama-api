"""The shape every SMS-confirmed action shares.

``POST`` without a code sends one and answers 202 with ``challenge_id``. The same ``POST``
with ``challenge_id`` and ``code`` does the action.
"""

from rest_framework import serializers, status
from rest_framework.response import Response

from apps.devices.signing import TTL


class ConfirmSerializer(serializers.Serializer):
    challenge_id = serializers.UUIDField(required=False)
    code = serializers.RegexField(r"^\d{6}$", required=False)

    def validate(self, attrs):
        if ("challenge_id" in attrs) != ("code" in attrs):
            raise serializers.ValidationError("Send both challenge_id and code, or neither.")
        return attrs


class ChallengeSentSerializer(serializers.Serializer):
    challenge_id = serializers.UUIDField()
    sent_to = serializers.CharField()
    expires_in = serializers.IntegerField()


def code_sent(challenge, user) -> Response:
    return Response({"challenge_id": str(challenge.id), "sent_to": user.phone_hint,
                     "expires_in": int(TTL.total_seconds())}, status=status.HTTP_202_ACCEPTED)
