"""Starting settings per chama type. A founder changes any of them before adoption.

Kept in code (not database rows) so a template change is reviewed like any other code
change; each version is frozen into the chama's RuleVersion at adoption.
"""

import copy

from apps.core import countries

from .schema import SCHEMA_VERSION

_BASE = {
    "schema_version": SCHEMA_VERSION,
    "contributions": {
        "amount_cents": 100_000,  # KES 1,000
        "frequency": "monthly",
        "due_day": 5,
        "grace_days": 2,
        "cash_banking_days": 3,
        "allocation_order": ["arrears", "fines", "current"],
    },
    "fines": {
        "late_contribution": {"enabled": True, "amount_cents": 20_000, "monthly_cap_cents": None},
        "stop_after_missed_months": 3,
    },
    "officials": {"roles": ["chair", "treasurer", "secretary"]},
    "approvals": {
        "tiers": [
            {"up_to_cents": 5_000_000, "officials": 2, "member_vote": False},
            {"up_to_cents": None, "officials": 2, "member_vote": True},
        ],
        "deadline_hours": 72,
    },
    "membership": {"entry_fee_cents": 0, "new_members_need_vote": False},
    "adoption": {"members_share_pct": 51},
    "statements": {"every_days": 14},
    "kyc": {"payer": "chama"},
    "votes": {"quorum_pct": 50, "pass_pct": 50, "secret": False},
    "rule_changes": {"notice_days": 14, "vote_days": 7, "quorum_pct": 50, "pass_pct": 66,
                     "cooling_days": 30},
    "waivers": {"officials": 2, "member_vote": False},
    "elections": {"term_months": 24, "vote_days": 7},
    "exit": {"notice_days": 30, "deduction_pct": 0, "death_benefit_cents": 0},
    "disputes": {"decided_by": "officials", "officials": 2, "response_hours": 72,
                 "allow_officials_only": True},
    "meetings": {"absence_fine_cents": 0, "late_fine_cents": 0, "late_after_minutes": 15,
                 "apology_excuses": True, "objection_days": 7},
}

TEMPLATE_VERSION = 1

TEMPLATES: dict[str, dict] = {
    "merry_go_round": {**_BASE, "rotation": {"order": "draw", "arrears": "skip"}},
    # Table banking lends to members (5.8); loans stay off behind the "loans" flag until
    # the lending opinion (decision 4).
    "table_banking": {**_BASE, "statements": {"every_days": 7},
                      "loans": {"min_months": 3, "savings_multiple_pct": 300,
                                "interest": "flat", "monthly_rate_bp": 100,
                                "max_term_months": 12, "guarantors": 2, "grace_days": 3,
                                "late_penalty_cents": 20_000, "default_after_missed": 2,
                                "contribution_first": True}},
    "welfare": {
        **_BASE,
        "contributions": {**_BASE["contributions"], "amount_cents": 50_000},
        "fines": {"late_contribution": {"enabled": True, "amount_cents": 10_000,
                                        "monthly_cap_cents": None}},
    },
    "investment_club": {
        **_BASE,
        "contributions": {**_BASE["contributions"], "amount_cents": 500_000},
        "fines": {"late_contribution": {"enabled": True, "amount_cents": 50_000,
                                        "monthly_cap_cents": None}},
        "statements": {"every_days": 31},
    },
}


def template_for(chama_type: str, currency: str = "KES") -> dict:
    """The starting rules for a type of chama, with amounts in ``currency``: the Kenyan
    figures scaled by a rough rate and rounded, so a Ugandan chama starts at UGX 28,000 a
    month rather than UGX 1,000. Officials set their own amounts during setup."""
    rules = copy.deepcopy(TEMPLATES[chama_type])
    rate = countries.KES_RATE.get(currency, 1)
    return rules if rate == 1 else _scale(rules, rate)


def _nice(cents: int, rate: float) -> int:
    """Two significant figures of the scaled amount, in whole units."""
    units = cents / 100 * rate
    if units <= 0:
        return 0
    step = 10 ** max(len(str(int(units))) - 2, 0)
    return int(round(units / step) * step * 100)


def _scale(value, rate):
    if isinstance(value, dict):
        return {k: (_nice(v, rate) if k.endswith("_cents") and isinstance(v, int)
                    else _scale(v, rate)) for k, v in value.items()}
    if isinstance(value, list):
        return [_scale(v, rate) for v in value]
    return value
