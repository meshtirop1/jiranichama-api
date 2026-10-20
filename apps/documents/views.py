"""Statements in the app, as sealed PDFs, and the public check page."""

from django.http import FileResponse
from django.template.loader import render_to_string
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.renderers import StaticHTMLRenderer
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.models import Membership
from apps.chamas.views import chama_for
from apps.core import countries
from apps.core.idempotency import idempotent
from apps.core.problems import Problem

from . import services, statements
from .models import DocumentAccess, IssuedDocument

PERIOD = [OpenApiParameter("from", OpenApiTypes.DATE, required=False),
          OpenApiParameter("to", OpenApiTypes.DATE, required=False)]


class IssueSerializer(serializers.Serializer):
    period_from = serializers.DateField(required=False)
    period_to = serializers.DateField(required=False)
    language = serializers.ChoiceField(choices=countries.TRANSLATED_CHOICES, required=False)


class DocumentSerializer(serializers.ModelSerializer):
    member_code = serializers.CharField(source="membership.member_code", default=None)
    verify_url = serializers.SerializerMethodField()
    seal_receipt = serializers.CharField(source="seal_entry.receipt_code")

    class Meta:
        model = IssuedDocument
        fields = ["id", "kind", "member_code", "period_from", "period_to", "language",
                  "verify_code", "verify_url", "fingerprint", "sha256", "seal_receipt",
                  "issued_at"]

    def get_verify_url(self, doc) -> str:
        return services.verify_url(doc.verify_code)


class CheckSerializer(serializers.Serializer):
    kind = serializers.CharField()
    chama_name = serializers.CharField()
    member_code = serializers.CharField(allow_null=True)
    period_from = serializers.CharField()
    period_to = serializers.CharField()
    issued_at = serializers.CharField()
    language = serializers.CharField()
    fingerprint = serializers.CharField()
    pdf_sha256 = serializers.CharField()
    sealed = serializers.BooleanField()
    seal_entry_seq = serializers.IntegerField()
    seal_receipt = serializers.CharField()
    file_matches = serializers.BooleanField(required=False)


def _membership(request, pk) -> tuple[Membership, Membership]:
    subject = Membership.objects.select_related("chama", "user").filter(pk=pk).first()
    if subject is None:
        raise Problem("not_found", "Member not found.", status.HTTP_404_NOT_FOUND)
    viewer = require_member(request.user, subject.chama)
    return subject, viewer


def _period(request):
    return request.query_params.get("from"), request.query_params.get("to")


def _issue_args(request, default_from):
    s = IssueSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    v = s.validated_data
    start, end = services.dates(
        v["period_from"].isoformat() if "period_from" in v else None,
        v["period_to"].isoformat() if "period_to" in v else None, default_from)
    return start, end, v.get("language") or request.user.language


class MemberStatementView(APIView):
    """The figures, for the statement screen. Any member may see any member's (the
    records are open, feature 6); each look is logged."""

    @extend_schema(parameters=PERIOD, responses=OpenApiTypes.OBJECT)
    def get(self, request, pk):
        subject, _ = _membership(request, pk)
        start, end = services.dates(*_period(request),
                                    statements.default_start(subject.chama, subject))
        services.log(DocumentAccess.Action.VIEW, chama=subject.chama, user=request.user,
                     subject=subject)
        return Response(statements.member_statement(subject, start, end))


class MemberStatementPdfView(APIView):
    @extend_schema(request=IssueSerializer, responses={201: DocumentSerializer})
    @idempotent()
    def post(self, request, pk):
        subject, viewer = _membership(request, pk)
        if not services.may_issue_member_statement(viewer, subject):
            raise Problem("not_allowed", "Only the member or an official can make this "
                                         "member's statement.", status.HTTP_403_FORBIDDEN)
        start, end, language = _issue_args(
            request, statements.default_start(subject.chama, subject))
        doc = services.issue(kind=IssuedDocument.Kind.MEMBER_STATEMENT, chama=subject.chama,
                             by_user=request.user, subject=subject, start=start, end=end,
                             language=language)
        return Response(DocumentSerializer(doc).data, status=status.HTTP_201_CREATED)


class ChamaStatementView(APIView):
    @extend_schema(parameters=PERIOD, responses=OpenApiTypes.OBJECT)
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        start, end = services.dates(*_period(request), statements.default_start(chama))
        services.log(DocumentAccess.Action.VIEW, chama=chama, user=request.user)
        return Response(statements.chama_statement(chama, start, end))


class ChamaStatementPdfView(APIView):
    @extend_schema(request=IssueSerializer, responses={201: DocumentSerializer})
    @idempotent()
    def post(self, request, pk):
        chama, _ = chama_for(request, pk)
        start, end, language = _issue_args(request, statements.default_start(chama))
        doc = services.issue(kind=IssuedDocument.Kind.CHAMA_STATEMENT, chama=chama,
                             by_user=request.user, subject=None, start=start, end=end,
                             language=language)
        return Response(DocumentSerializer(doc).data, status=status.HTTP_201_CREATED)


def _document_for(request, pk) -> IssuedDocument:
    doc = IssuedDocument.objects.select_related("chama", "membership", "seal_entry").filter(
        pk=pk).first()
    if doc is None:
        raise Problem("not_found", "Document not found.", status.HTTP_404_NOT_FOUND)
    viewer = require_member(request.user, doc.chama)
    if doc.membership_id and not services.may_issue_member_statement(viewer, doc.membership):
        raise Problem("not_found", "Document not found.", status.HTTP_404_NOT_FOUND)
    return doc


class DocumentView(APIView):
    @extend_schema(responses=DocumentSerializer)
    def get(self, request, pk):
        return Response(DocumentSerializer(_document_for(request, pk)).data)


class DocumentFileView(APIView):
    @extend_schema(responses={(200, "application/pdf"): OpenApiTypes.BINARY})
    def get(self, request, pk):
        doc = _document_for(request, pk)
        services.log(DocumentAccess.Action.DOWNLOAD, chama=doc.chama, user=request.user,
                     subject=doc.membership, document=doc)
        kind = doc.kind.replace("_", "-")
        return FileResponse(doc.file.open("rb"), as_attachment=True,
                            filename=f"JiraniChama-{kind}-{doc.verify_code}.pdf",
                            content_type="application/pdf")


def _checked(code, file_bytes=None) -> dict:
    doc = services.find(code)
    if doc is None:
        raise Problem("not_found", "No statement has this code.", status.HTTP_404_NOT_FOUND)
    services.log(DocumentAccess.Action.VERIFY, chama=doc.chama, document=doc,
                 subject=doc.membership)
    return services.check(doc, file_bytes)


class VerifyView(APIView):
    """Public: anyone holding a statement (a bank, a lender, a member) can check it."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "verify"
    parser_classes = [MultiPartParser]

    @extend_schema(responses=CheckSerializer)
    def get(self, request, code):
        return Response(_checked(code))

    @extend_schema(request={"multipart/form-data": {
        "type": "object", "properties": {"file": {"type": "string", "format": "binary"}}}},
        responses=CheckSerializer)
    def post(self, request, code):
        upload = request.FILES.get("file")
        if upload is None or upload.size > 5 * 1024 * 1024:
            raise Problem("file_required", "Attach the PDF (up to 5 MB) as 'file'.",
                          status.HTTP_400_BAD_REQUEST)
        return Response(_checked(code, upload.read()))


class VerifyPageView(APIView):
    """What a phone camera opens from the QR code: the same check, as a page."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "verify"
    renderer_classes = [StaticHTMLRenderer]

    @extend_schema(exclude=True)
    def get(self, request, code):
        try:
            result = _checked(code)
        except Problem:
            result = None
        html = render_to_string("documents/verify.html",
                                {"r": result, "code": services.normalise_code(code)})
        return Response(html, status=200 if result else 404)
