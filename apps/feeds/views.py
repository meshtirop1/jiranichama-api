from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.views import chama_for
from apps.core.idempotency import idempotent
from apps.core.problems import Problem
from apps.core.two_step import ChallengeSentSerializer, ConfirmSerializer, code_sent

from . import services
from .models import Statement


class StatementSerializer(serializers.ModelSerializer):
    lines = serializers.SerializerMethodField()
    credits_cents = serializers.SerializerMethodField()
    debits_cents = serializers.SerializerMethodField()
    uploaded_by = serializers.CharField(source="uploaded_by.display_name", default=None)
    confirmed_by = serializers.CharField(source="confirmed_by.display_name", default=None)

    class Meta:
        model = Statement
        fields = ["id", "route", "state", "filename", "parser", "period_from", "period_to",
                  "opening_cents", "closing_cents", "credits_cents", "debits_cents", "lines",
                  "problems", "flags", "uploaded_by", "confirmed_by", "confirmed_at",
                  "created_at"]

    def get_lines(self, obj) -> int:
        return len(obj.parsed_lines)

    def get_credits_cents(self, obj) -> int:
        return sum(line["credit_cents"] for line in obj.parsed_lines)

    def get_debits_cents(self, obj) -> int:
        return sum(line["debit_cents"] for line in obj.parsed_lines)


class StatementListView(APIView):
    parser_classes = [MultiPartParser]

    @extend_schema(responses=StatementSerializer(many=True))
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        return Response(StatementSerializer(
            chama.statements.select_related("uploaded_by", "confirmed_by")
            .order_by("-created_at"), many=True).data)

    @extend_schema(request={"multipart/form-data": {"type": "object", "properties": {
        "file": {"type": "string", "format": "binary"}}}},
        responses={201: StatementSerializer})
    @idempotent()
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        f = request.FILES.get("file")
        if f is None:
            raise Problem("file_required", "Attach the statement file.")
        if f.size > settings.STATEMENT_MAX_BYTES:
            raise Problem("file_too_large", "The file is too large.",
                          status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        statement = services.upload(chama, me, filename=f.name, data=f.read())
        return Response(StatementSerializer(statement).data, status=status.HTTP_201_CREATED)


def _statement_for(request, pk) -> tuple[Statement, object]:
    statement = Statement.objects.select_related("chama").filter(pk=pk).first()
    if statement is None:
        raise Problem("not_found", "Statement not found.", status.HTTP_404_NOT_FOUND)
    return statement, require_member(request.user, statement.chama)


class StatementDetailView(APIView):
    @extend_schema(responses=StatementSerializer)
    def get(self, request, pk):
        statement, _ = _statement_for(request, pk)
        data = StatementSerializer(statement).data
        data["parsed_lines"] = statement.parsed_lines
        return Response(data)


class StatementConfirmView(APIView):
    @extend_schema(request=ConfirmSerializer,
                   responses={200: StatementSerializer, 202: ChallengeSentSerializer})
    def post(self, request, pk):
        statement, me = _statement_for(request, pk)
        s = ConfirmSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if "code" not in s.validated_data:
            return code_sent(services.start_confirm(statement, me), request.user)
        statement = services.confirm(statement, me, challenge_id=s.validated_data["challenge_id"],
                                     sms_code=s.validated_data["code"])
        return Response(StatementSerializer(statement).data)


class MailInboundHook(APIView):
    """Inbound bank e-statements from the mail provider. Checked by a secret path token
    and an optional source-IP allowlist; answers fast and never says why it refused."""

    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []

    @extend_schema(exclude=True)
    def post(self, request, token):
        if not services.hook_token_ok(token):
            return Response(status=status.HTTP_404_NOT_FOUND)
        allowed = settings.MAIL_HOOK_ALLOWED_IPS
        if allowed and request.META.get("REMOTE_ADDR") not in allowed:
            return Response(status=status.HTTP_404_NOT_FOUND)
        statements = services.receive_mail(request.data if isinstance(request.data, dict)
                                           else {})
        return Response({"received": len(statements)}, status=status.HTTP_202_ACCEPTED)
