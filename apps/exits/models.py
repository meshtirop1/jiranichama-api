import uuid

from django.db import models

from apps.chamas.models import Chama, Membership
from apps.ledger.models import LedgerEntry


class Exit(models.Model):
    """A member leaving, or a member who died (5.13), and the settlement of their money."""

    class Kind(models.TextChoices):
        LEAVE = "leave"
        DEATH = "death"

    class State(models.TextChoices):
        NOTICE = "notice"        # leaving; the notice period runs
        SETTLING = "settling"    # the settlement is with the officials as money out
        SETTLED = "settled"
        CANCELLED = "cancelled"  # the member changed their mind during notice

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="exits")
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="exits")
    kind = models.CharField(max_length=5, choices=Kind.choices)
    recorded_by = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    exit_on = models.DateField()
    quote = models.JSONField(default=dict)  # the calculation shown, then the one settled
    state = models.CharField(max_length=10, choices=State.choices, default=State.NOTICE)
    proposal = models.ForeignKey("payouts.Proposal", on_delete=models.PROTECT, null=True,
                                 blank=True, related_name="+")
    benefit_proposal = models.ForeignKey("payouts.Proposal", on_delete=models.PROTECT,
                                         null=True, blank=True, related_name="+")
    # Death only.
    died_on = models.DateField(null=True, blank=True)
    # Encrypted at rest (privacy, 2026-10-20): a grieving family's name and number are
    # nobody's business but the officials paying them. Read with ``next_of_kin``.
    next_of_kin_enc = models.BinaryField(null=True, blank=True)
    document = models.FileField(upload_to="exits/%Y/%m/", blank=True)
    document_sha256 = models.CharField(max_length=64, blank=True)
    entry = models.ForeignKey(LedgerEntry, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE = ["state", "quote", "proposal_id", "benefit_proposal_id"]

    class Meta:
        db_table = "exits_exit"

    def set_next_of_kin(self, name: str, phone: str) -> None:
        import json

        from apps.core.crypto import encrypt

        self.next_of_kin_enc = encrypt(json.dumps({"name": name, "phone": phone}))

    @property
    def next_of_kin(self) -> tuple[str, str]:
        import json

        from apps.core.crypto import decrypt

        if not self.next_of_kin_enc:
            return "", ""
        d = json.loads(decrypt(bytes(self.next_of_kin_enc)))
        return d.get("name", ""), d.get("phone", "")

    @property
    def next_of_kin_name(self) -> str:
        return self.next_of_kin[0]

    @property
    def next_of_kin_phone(self) -> str:
        return self.next_of_kin[1]


class DeathConfirmation(models.Model):
    """A second official confirming a reported death. The official who reported it cannot
    confirm it, and nothing is settled without one (protection plan 2026-10-20)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exit = models.ForeignKey(Exit, on_delete=models.PROTECT, related_name="confirmations")
    by = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "exits_deathconfirmation"
        constraints = [models.UniqueConstraint(fields=["exit", "by"],
                                               name="death_confirmed_once_per_official")]

