"""What a phone needs to check its chama's chain (6.10 "On-phone verification")."""

import base64

from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.views import chama_for

from .models import ChainHead, LedgerEntry
from .services import document_for
from .signing import get_signer, public_key_pem

PAGE = 500


class LedgerItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    document = serializers.JSONField(help_text="The exact object that was hashed.")
    hash = serializers.CharField()
    receipt_code = serializers.CharField()
    platform_sig = serializers.CharField(allow_null=True, help_text="Base64 DER signature.")
    key_id = serializers.CharField(allow_null=True)


class LedgerPageSerializer(serializers.Serializer):
    entries = LedgerItemSerializer(many=True)
    more = serializers.BooleanField()
    next_after_seq = serializers.IntegerField()


class LedgerHeadSerializer(serializers.Serializer):
    seq = serializers.IntegerField()
    hash = serializers.CharField()


class PlatformKeySerializer(serializers.Serializer):
    key_id = serializers.CharField()
    algorithm = serializers.CharField()
    public_key_pem = serializers.CharField()


class LedgerView(APIView):
    @extend_schema(responses=LedgerPageSerializer)
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        try:
            after = max(int(request.query_params.get("after_seq", 0)), 0)
        except ValueError:
            after = 0
        entries = list(LedgerEntry.objects.filter(chama=chama, seq__gt=after)
                       .select_related("platform_signature").order_by("seq")[:PAGE + 1])
        more = len(entries) > PAGE
        out = []
        for e in entries[:PAGE]:
            sig = getattr(e, "platform_signature", None)
            out.append({
                "id": str(e.id),
                "document": document_for(e),
                "hash": e.hash,
                "receipt_code": e.receipt_code,
                "platform_sig": base64.b64encode(bytes(sig.platform_sig)).decode() if sig else None,
                "key_id": sig.key_id if sig else None,
            })
        return Response({"entries": out, "more": more,
                         "next_after_seq": out[-1]["document"]["seq"] if out else after})


class LedgerHeadView(APIView):
    @extend_schema(responses=LedgerHeadSerializer)
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        head = ChainHead.objects.filter(chama=chama).first()
        return Response({"seq": head.seq if head else 0,
                         "hash": head.hash if head else "0" * 64})


class PlatformKeyView(APIView):
    """The public key that verifies every platform signature. Public on purpose."""

    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []

    @extend_schema(responses=PlatformKeySerializer)
    def get(self, request):
        signer = get_signer()
        return Response({"key_id": signer.key_id, "algorithm": "ECDSA_P256_SHA256_PREHASHED",
                         "public_key_pem": public_key_pem(signer.public_key())})


class AnchorProofView(APIView):
    """This chama's place in the latest public anchors: the leaf and Merkle path, so the
    phone can check its chain head is in that night's root."""

    @extend_schema(responses=serializers.ListSerializer(child=serializers.DictField()))
    def get(self, request, pk):
        from .anchoring import proof_for
        from .models import Anchor

        chama, _ = chama_for(request, pk)
        out = []
        for a in Anchor.objects.order_by("-at")[:7]:
            proof = proof_for(a, chama.id)
            if proof:
                out.append(proof)
        return Response(out)
