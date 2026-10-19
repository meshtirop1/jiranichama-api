"""Statements issued as PDFs, and every time someone looks at one (4.8, 6.6)."""

import uuid

from django.conf import settings
from django.db import models

from apps.chamas.models import Chama, Membership
from apps.ledger.models import LedgerEntry


class IssuedDocument(models.Model):
    """One PDF we produced. Written once; the database refuses UPDATE and DELETE.

    ``fingerprint`` is the SHA-256 of the statement's figures (canonical JSON), printed
    on every page. ``sha256`` is the hash of the PDF bytes. Both are written into the
    chama's chain as a ``seal`` entry, so a statement cannot be quietly re-issued with
    other figures.
    """

    class Kind(models.TextChoices):
        MEMBER_STATEMENT = "member_statement"
        CHAMA_STATEMENT = "chama_statement"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="documents")
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, null=True,
                                   blank=True, related_name="documents")
    kind = models.CharField(max_length=24, choices=Kind.choices)
    period_from = models.DateField()
    period_to = models.DateField()
    language = models.CharField(max_length=2)
    data = models.JSONField()
    fingerprint = models.CharField(max_length=64)
    sha256 = models.CharField(max_length=64)
    verify_code = models.CharField(max_length=10, unique=True)
    file = models.FileField(upload_to="documents/%Y/%m/")
    issued_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                  related_name="+")
    seal_entry = models.OneToOneField(LedgerEntry, on_delete=models.PROTECT,
                                      related_name="sealed_document")
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "documents_issued"
        indexes = [models.Index(fields=["chama", "kind", "fingerprint"])]


class DocumentAccess(models.Model):
    """Who looked at which statement, and when. Every view is logged (4.8)."""

    class Action(models.TextChoices):
        VIEW = "view"          # the figures were shown in the app
        ISSUE = "issue"        # a PDF was asked for
        DOWNLOAD = "download"  # the PDF file was fetched
        VERIFY = "verify"      # someone checked a code (no sign-in needed)

    id = models.BigAutoField(primary_key=True)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="+")
    subject = models.ForeignKey(Membership, on_delete=models.PROTECT, null=True, blank=True,
                                related_name="+")
    document = models.ForeignKey(IssuedDocument, on_delete=models.PROTECT, null=True,
                                 blank=True, related_name="accesses")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                             blank=True, related_name="+")
    action = models.CharField(max_length=12, choices=Action.choices)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "documents_access"
