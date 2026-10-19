import secrets
import uuid

from django.db import models

from apps.chamas.models import Chama, Membership
from apps.ledger.models import LedgerEntry


def _qr_secret() -> str:
    return secrets.token_hex(16)


class Meeting(models.Model):
    """A chama meeting (5.11): scheduled by the secretary, attendance by QR check-in or the
    secretary's marks, absence and lateness fines on closing, then minutes members may
    object to for a week before they are sealed."""

    class State(models.TextChoices):
        SCHEDULED = "scheduled"
        IN_PROGRESS = "in_progress"
        ATTENDANCE_CLOSED = "attendance_closed"  # waiting for minutes
        MINUTES_PENDING = "minutes_pending"      # minutes out, objection window open
        SEALED = "sealed"
        CANCELLED = "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="meetings")
    scheduled_by = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    title = models.CharField(max_length=120)
    starts_at = models.DateTimeField()
    place = models.CharField(max_length=200, blank=True)
    online_link = models.URLField(blank=True)
    agenda = models.TextField(blank=True)
    state = models.CharField(max_length=20, choices=State.choices, default=State.SCHEDULED)
    qr_secret = models.CharField(max_length=32, default=_qr_secret)
    started_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_entry = models.ForeignKey(LedgerEntry, on_delete=models.PROTECT, null=True,
                                     blank=True, related_name="+")
    sealed_entry = models.ForeignKey(LedgerEntry, on_delete=models.PROTECT, null=True,
                                     blank=True, related_name="+")
    cancel_reason = models.CharField(max_length=300, blank=True)
    reminded_2d_at = models.DateTimeField(null=True, blank=True)
    reminded_2h_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # What may change after scheduling; the title, time, place and agenda members were
    # told about may not (cancel and schedule again instead).
    MUTABLE = ["state", "started_at", "closed_at", "closed_entry_id", "sealed_entry_id",
               "cancel_reason", "reminded_2d_at", "reminded_2h_at"]

    class Meta:
        db_table = "meetings_meeting"
        indexes = [models.Index(fields=["chama", "starts_at"])]


class Apology(models.Model):
    """Sent before the meeting starts, by the member or recorded by the secretary."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    meeting = models.ForeignKey(Meeting, on_delete=models.PROTECT, related_name="apologies")
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=300)
    recorded_by = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "meetings_apology"
        constraints = [models.UniqueConstraint(fields=["meeting", "membership"],
                                               name="one_apology_per_meeting")]


class Attendance(models.Model):
    class Method(models.TextChoices):
        QR = "qr"                # scanned or typed the code on the secretary's phone
        SECRETARY = "secretary"  # marked by the secretary

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    meeting = models.ForeignKey(Meeting, on_delete=models.PROTECT, related_name="attendance")
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    method = models.CharField(max_length=10, choices=Method.choices)
    late = models.BooleanField(default=False)
    marked_by = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "meetings_attendance"
        constraints = [models.UniqueConstraint(fields=["meeting", "membership"],
                                               name="attend_once")]


class Minutes(models.Model):
    """One version of the minutes. A revision after objections is a new version, which
    restarts the objection window; versions are never edited."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    meeting = models.ForeignKey(Meeting, on_delete=models.PROTECT, related_name="minutes")
    version = models.PositiveSmallIntegerField()
    text = models.TextField(blank=True)
    photo = models.FileField(upload_to="minutes/%Y/%m/", blank=True)
    photo_sha256 = models.CharField(max_length=64, blank=True)
    hash = models.CharField(max_length=64)
    written_by = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    objection_until = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "meetings_minutes"
        constraints = [models.UniqueConstraint(fields=["meeting", "version"],
                                               name="minutes_version_once")]


class MinutesObjection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    minutes = models.ForeignKey(Minutes, on_delete=models.PROTECT, related_name="objections")
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "meetings_minutes_objection"
        constraints = [models.UniqueConstraint(fields=["minutes", "membership"],
                                               name="object_once_per_version")]
