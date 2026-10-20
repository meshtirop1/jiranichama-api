from django.http import FileResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.problems import Problem
from apps.core.two_step import ChallengeSentSerializer, ConfirmSerializer, code_sent

from . import mandate
from .access import require_member
from .models import BankMandate
from .views import chama_for


def _mandate_for(request, pk) -> tuple[BankMandate, object]:
    m = BankMandate.objects.select_related("chama").filter(pk=pk).first()
    if m is None:
        raise Problem("not_found", "Mandate not found.", status.HTTP_404_NOT_FOUND)
    return m, require_member(request.user, m.chama)


def _state(chama) -> dict:
    pending = (BankMandate.objects.filter(chama=chama, state=BankMandate.State.PROPOSED)
               .first())
    current = mandate.current(chama)
    return {
        "current": mandate.as_dict(current),
        "pending": mandate.as_dict(pending),
        "min_signers_required": mandate.MIN_SIGNERS,
        # The safest accounts: every signatory approves in the bank's own app.
        "multi_approval_options": mandate.MULTI_APPROVAL.get(chama.country, []),
        "safe": bool(current and current.min_signers >= mandate.MIN_SIGNERS),
    }


class BankMandateView(APIView):
    """The chama's bank mandate: who may sign at the bank and how many must sign."""

    parser_classes = [MultiPartParser, JSONParser]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        return Response(_state(chama))

    @extend_schema(request={"multipart/form-data": {"type": "object", "properties": {
        "file": {"type": "string", "format": "binary"},
        "signatory_ids": {"type": "array", "items": {"type": "string", "format": "uuid"}},
        "min_signers": {"type": "integer"},
        "bank_app_approvals": {"type": "boolean"}}}},
        responses={201: OpenApiTypes.OBJECT})
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        f = request.FILES.get("file")
        if f is None:
            raise Problem("file_required", "Attach a photo or PDF of the bank's mandate.")
        ids = request.data.getlist("signatory_ids") if hasattr(request.data, "getlist") \
            else request.data.get("signatory_ids", [])
        if len(ids) == 1 and "," in ids[0]:
            ids = [i.strip() for i in ids[0].split(",") if i.strip()]
        try:
            min_signers = int(request.data.get("min_signers", 0))
        except (TypeError, ValueError):
            min_signers = 0
        approvals = str(request.data.get("bank_app_approvals", "")).lower() in ("1", "true")
        mandate.propose(chama, me, signatory_ids=ids, min_signers=min_signers,
                        bank_app_approvals=approvals, filename=f.name, data=f.read())
        return Response(_state(chama), status=status.HTTP_201_CREATED)


class BankMandateConfirmView(APIView):
    @extend_schema(request=ConfirmSerializer,
                   responses={200: OpenApiTypes.OBJECT, 202: ChallengeSentSerializer})
    def post(self, request, pk):
        m, me = _mandate_for(request, pk)
        s = ConfirmSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if "code" not in s.validated_data:
            return code_sent(mandate.start_confirm(m, me), request.user)
        mandate.confirm(m, me, challenge_id=s.validated_data["challenge_id"],
                        sms_code=s.validated_data["code"])
        return Response(_state(m.chama))


class BankMandateRejectView(APIView):
    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    def post(self, request, pk):
        m, me = _mandate_for(request, pk)
        mandate.reject(m, me)
        return Response(_state(m.chama))


class BankMandateFileView(APIView):
    @extend_schema(responses={(200, "application/octet-stream"): OpenApiTypes.BINARY})
    def get(self, request, pk):
        m, _ = _mandate_for(request, pk)
        return FileResponse(m.document.open("rb"), as_attachment=True,
                            filename=f"JiraniChama-bank-mandate-{m.document_sha256[:8]}"
                                     f"{m.document.name[-4:]}")
