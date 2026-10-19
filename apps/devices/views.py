"""A person's phone signing keys (6.7)."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.problems import Problem
from apps.core.two_step import ChallengeSentSerializer, code_sent

from . import keys
from .models import Device, DeviceNonce


class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = ["id", "platform", "label", "fingerprint", "security_level",
                  "attestation_root_trusted", "state", "registered_at", "revoked_at"]


class RegisterSerializer(serializers.Serializer):
    public_key = serializers.CharField(help_text="Base64 SubjectPublicKeyInfo, P-256.")
    platform = serializers.ChoiceField(choices=["android", "ios"])
    label = serializers.CharField(required=False, allow_blank=True, max_length=80)
    attestation_chain = serializers.ListField(child=serializers.CharField(), required=False,
                                              help_text="Base64 DER certificates, leaf first.")
    attest_nonce = serializers.CharField()
    challenge_id = serializers.UUIDField(required=False)
    code = serializers.CharField(required=False, max_length=6)


class NonceSerializer(serializers.Serializer):
    nonce = serializers.CharField()


class DeviceListView(APIView):
    @extend_schema(responses=DeviceSerializer(many=True))
    def get(self, request):
        return Response(DeviceSerializer(
            Device.objects.filter(user=request.user).order_by("-registered_at"), many=True).data)

    @extend_schema(request=RegisterSerializer,
                   responses={201: DeviceSerializer, 202: ChallengeSentSerializer})
    def post(self, request):
        """Two steps: without a code, an SMS code is sent; with it, the key is registered."""
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        if "code" not in v:
            return code_sent(keys.start_register(request.user, public_key_b64=v["public_key"]),
                             request.user)
        if "challenge_id" not in v:
            raise Problem("challenge_required", "Send the challenge_id with the code.")
        device = keys.register(request.user, public_key_b64=v["public_key"],
                               platform=v["platform"], label=v.get("label", ""),
                               chain_b64=v.get("attestation_chain", []),
                               attest_nonce=v["attest_nonce"],
                               challenge_id=v["challenge_id"], sms_code=v["code"])
        return Response(DeviceSerializer(device).data, status=status.HTTP_201_CREATED)


class AttestNonceView(APIView):
    """The challenge the phone puts in its key's attestation when it makes the key."""

    @extend_schema(request=None, responses=NonceSerializer)
    def post(self, request):
        return Response({"nonce": keys.issue_nonce(request.user, DeviceNonce.Use.ATTEST).nonce})


class DeviceRevokeView(APIView):
    @extend_schema(request=None, responses=DeviceSerializer)
    def post(self, request, pk):
        device = Device.objects.filter(pk=pk, user=request.user).first()
        if device is None:
            raise Problem("not_found", "Device not found.", status.HTTP_404_NOT_FOUND)
        return Response(DeviceSerializer(keys.revoke(device, request.user)).data)
