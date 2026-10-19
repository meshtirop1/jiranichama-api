from django.conf import settings
from drf_spectacular.utils import extend_schema, extend_schema_field
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
from .models import Statement, TypedLine


class TypedLineSerializer(serializers.ModelSerializer):
    typed_by = serializers.CharField(read_only=True, default=None)

    class Meta:
        model = TypedLine
        fields = ["line_no", "value_date", "narration", "reference", "credit_cents",
                  "debit_cents", "balance_cents", "typed_by", "typed_at"]


class TypedLineInSerializer(serializers.Serializer):
    line_no = serializers.IntegerField()
    value_date = serializers.DateField()
    narration = serializers.CharField(required=False, allow_blank=True, max_length=200)
    reference = serializers.CharField(required=False, allow_blank=True, max_length=100)
    credit_cents = serializers.IntegerField(required=False, min_value=0, default=0)
    debit_cents = serializers.IntegerField(required=False, min_value=0, default=0)
    balance_cents = serializers.IntegerField(required=False, allow_null=True, default=None)


class TypedLinesSerializer(serializers.Serializer):
    lines = TypedLineInSerializer(many=True)


class StatementSerializer(serializers.ModelSerializer):
    lines = serializers.SerializerMethodField()
    credits_cents = serializers.SerializerMethodField()
    debits_cents = serializers.SerializerMethodField()
    uploaded_by = serializers.SerializerMethodField()
    confirmed_by = serializers.SerializerMethodField()
    unreadable_lines = serializers.JSONField(
        help_text="Rows the parser could not read: [{line_no, cells}]. While the state is "
                  "needs_typing an official types them in.")
    typed_lines = serializers.SerializerMethodField()

    class Meta:
        model = Statement
        fields = ["id", "route", "state", "filename", "parser", "period_from", "period_to",
                  "opening_cents", "closing_cents", "credits_cents", "debits_cents", "lines",
                  "problems", "flags", "uploaded_by", "confirmed_by", "confirmed_at",
                  "created_at", "unreadable_lines", "typed_lines"]

    def _who(self, obj, user) -> str | None:
        if user is None:
            return None
        if user.display_name:
            return user.display_name
        m = obj.chama.memberships.filter(user=user).only("member_code").first()
        return m.member_code if m else user.phone_hint

    def get_uploaded_by(self, obj) -> str | None:
        return self._who(obj, obj.uploaded_by)

    def get_confirmed_by(self, obj) -> str | None:
        return self._who(obj, obj.confirmed_by)

    def _typed(self, obj):
        return list(obj.typed_lines.select_related("typed_by").order_by("line_no"))

    def get_lines(self, obj) -> int:
        return len(obj.parsed_lines) + len(self._typed(obj))

    def get_credits_cents(self, obj) -> int:
        return (sum(line["credit_cents"] for line in obj.parsed_lines)
                + sum(t.credit_cents for t in self._typed(obj)))

    def get_debits_cents(self, obj) -> int:
        return (sum(line["debit_cents"] for line in obj.parsed_lines)
                + sum(t.debit_cents for t in self._typed(obj)))

    @extend_schema_field(TypedLineSerializer(many=True))
    def get_typed_lines(self, obj) -> list:
        return [{**TypedLineSerializer(t).data, "typed_by": self._who(obj, t.typed_by)}
                for t in self._typed(obj)]


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


class StatementTypedLinesView(APIView):
    """Type in the lines the parser could not read (5.5). All at once; refused, with the
    reasons, unless the statement then adds up."""

    @extend_schema(request=TypedLinesSerializer, responses=StatementSerializer)
    @idempotent()
    def post(self, request, pk):
        statement, me = _statement_for(request, pk)
        s = TypedLinesSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        lines = [{**line, "value_date": line["value_date"].isoformat()}
                 for line in s.validated_data["lines"]]
        statement = services.submit_typed_lines(statement, me, lines)
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
