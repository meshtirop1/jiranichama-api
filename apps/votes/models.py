"""Members' votes (5.11). Phase 2 opens them for payouts above the rules' vote level;
rule changes, elections and loans reuse them later."""

import uuid

from django.db import models

from apps.chamas.models import Chama, Membership
from apps.ledger.models import LedgerEntry


class Vote(models.Model):
    """One question put to the members. Its settings are copied from the rules when it
    opens, so a rule change cannot alter a vote already running. Only the state, the
    tally and the result entry change afterwards."""

    class State(models.TextChoices):
        OPEN = "open"
        PASSED = "passed"
        FAILED = "failed"    # enough members voted, not enough said yes
        LAPSED = "lapsed"    # not enough members voted

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="votes")
    subject = models.CharField(max_length=80)  # "payouts.proposal:<uuid>"
    question = models.CharField(max_length=300)
    quorum_pct = models.PositiveSmallIntegerField()
    pass_pct = models.PositiveSmallIntegerField()
    secret = models.BooleanField(default=False)
    # Rule changes: "pass" means that share of everyone eligible said yes, not only of those
    # who voted, so a few members cannot change the rules for everyone (2026-10-20).
    over_all = models.BooleanField(default=False)
    eligible = models.PositiveIntegerField()
    # Who may vote, decided when it opens (members, not the person being paid).
    eligible_ids = models.JSONField(default=list)
    closes_at = models.DateTimeField()
    state = models.CharField(max_length=8, choices=State.choices, default=State.OPEN)
    yes = models.PositiveIntegerField(default=0)
    no = models.PositiveIntegerField(default=0)
    result_entry = models.OneToOneField(LedgerEntry, on_delete=models.PROTECT, null=True,
                                        blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE = ["state", "yes", "no", "result_entry_id"]

    class Meta:
        db_table = "votes_vote"
        indexes = [models.Index(fields=["chama", "state"])]


class Ballot(models.Model):
    """One member's vote, confirmed by their phone key or an SMS code. Insert only; the
    first ballot counts.

    In a secret vote the ballot is stored without the member: ``voter_hash`` (a keyed hash
    of vote and member) stops a second ballot, and the list of who voted is kept apart
    (``Turnout``), so choices are not stored with names."""

    class Choice(models.TextChoices):
        YES = "yes"
        NO = "no"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vote = models.ForeignKey(Vote, on_delete=models.PROTECT, related_name="ballots")
    voter_hash = models.CharField(max_length=80)
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, null=True,
                                   blank=True, related_name="+")
    choice = models.CharField(max_length=3, choices=Choice.choices)
    method = models.CharField(max_length=8)  # device, sms (code) or reply (SMS reply)
    device = models.ForeignKey("devices.Device", on_delete=models.PROTECT, null=True,
                               blank=True, related_name="+")
    payload_hash = models.CharField(max_length=64, blank=True, default="")
    signature = models.BinaryField(null=True, blank=True)

    class Meta:
        db_table = "votes_ballot"
        constraints = [models.UniqueConstraint(fields=["vote", "voter_hash"],
                                               name="one_ballot_per_member")]


class Turnout(models.Model):
    """Who has voted (public in every vote), without what they chose. Insert only."""

    id = models.BigAutoField(primary_key=True)
    vote = models.ForeignKey(Vote, on_delete=models.PROTECT, related_name="turnout")
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "votes_turnout"
        constraints = [models.UniqueConstraint(fields=["vote", "membership"],
                                               name="voted_once")]


class VoteSmsNumber(models.Model):
    """The number a member replies with to vote by SMS ("YES 4821"). Unique among the
    member's votes, so a reply can only mean one vote; it works only from their own
    number. Insert only."""

    id = models.BigAutoField(primary_key=True)
    vote = models.ForeignKey(Vote, on_delete=models.PROTECT, related_name="sms_numbers")
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    number = models.CharField(max_length=4)

    class Meta:
        db_table = "votes_sms_number"
        constraints = [models.UniqueConstraint(fields=["vote", "membership"],
                                               name="one_number_per_member")]
