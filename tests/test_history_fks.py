"""Records outlive accounts (6.1 principle 9).

Any foreign key that touches a history table must be PROTECT. CASCADE would delete
history; SET_NULL would try to UPDATE an append-only row and fail at the worst moment.
"""

from django.apps import apps
from django.db import models

HISTORY_MODELS = {
    "ledger.LedgerEntry",
    "ledger.EntrySignature",
    "ledger.ChainHead",
    "ledger.Anchor",
    "staff.AuditLog",
    "chamas.Chama",
    "chamas.Membership",
    "chamas.Invite",
    "chamas.BankAccount",
    "chamas.StatementInbox",
    "rules.RuleVersion",
    "rules.RuleProposal",
    "fines.Fine",
    "feeds.Statement",
    "feeds.BankTransaction",
    "reconciliation.Match",
    "contributions.OpeningBalance",
    "contributions.OpeningBalanceLine",
    "contributions.OpeningObjection",
    "contributions.CashReceipt",
    "documents.IssuedDocument",
    "feeds.TypedLine",
    "payouts.Proposal",
    "payouts.Approval",
    "payouts.UnapprovedDebit",
    "payouts.MoneyOutConsent",
    "payouts.Rotation",
    "payouts.RotationTurn",
    "devices.Device",
    "reconciliation.ReconciliationRun",
    "reconciliation.ReconciliationGap",
    "reconciliation.StatementReminder",
    "votes.Vote",
    "votes.Ballot",
    "votes.Turnout",
    "votes.VoteSmsNumber",
    "notify.InboundSms",
    "notify.UssdRequest",
    "documents.DocumentAccess",
    "meetings.Meeting",
    "meetings.Apology",
    "meetings.Attendance",
    "meetings.Minutes",
    "meetings.MinutesObjection",
    "loans.Loan",
    "loans.Guarantee",
    "loans.LoanReminder",
    "fines.Waiver",
    "fines.WaiverDecision",
    "disputes.Dispute",
    "disputes.DisputeMessage",
    "devices.DeviceApproval",
    "elections.Election",
    "exits.Exit",
}
OUR_APPS = {"core", "flags", "accounts", "notify", "chamas", "ledger", "staff", "devices",
            "rules", "fines", "contributions", "feeds", "reconciliation", "documents",
            "payouts", "votes", "meetings", "loans", "disputes", "elections", "exits"}


def _label(model) -> str:
    return f"{model._meta.app_label}.{model.__name__}"


def test_every_fk_touching_history_is_protect():
    offenders = []
    for model in apps.get_models():
        if model._meta.app_label not in OUR_APPS:
            continue
        for field in model._meta.get_fields():
            if not isinstance(field, models.ForeignKey) or not field.concrete:
                continue
            if _label(model) in HISTORY_MODELS or _label(field.related_model) in HISTORY_MODELS:
                if field.remote_field.on_delete is not models.PROTECT:
                    offenders.append(f"{_label(model)}.{field.name}")
    assert offenders == []


def test_user_rows_are_protected_by_history():
    from apps.accounts.models import User

    protected_by = {
        _label(model)
        for model in apps.get_models()
        for field in model._meta.get_fields()
        if isinstance(field, models.ForeignKey) and field.related_model is User
        and field.remote_field.on_delete is models.PROTECT
    }
    assert {"chamas.Membership", "chamas.Chama", "staff.AuditLog"} <= protected_by
