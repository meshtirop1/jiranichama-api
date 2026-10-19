"""Starting settings per chama type. A founder changes any of them before adoption.

Kept in code (not database rows) so a template change is reviewed like any other code
change; each version is frozen into the chama's RuleVersion at adoption.
"""

import copy

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
}

TEMPLATE_VERSION = 1

TEMPLATES: dict[str, dict] = {
    "merry_go_round": _BASE,
    "table_banking": {**_BASE, "statements": {"every_days": 7}},
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


def template_for(chama_type: str) -> dict:
    return copy.deepcopy(TEMPLATES[chama_type])
