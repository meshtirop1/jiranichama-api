"""Rules schema v1: a chama's constitution as settings.

Phase 1 covers contributions, late fines, officials, approvals (stored and checked; used
from Phase 2), membership, adoption, statements and who pays for ID checks. Loans,
meetings and rule changes join in Phase 3 as new sections with a new schema version.
"""

SCHEMA_VERSION = 1

_int = {"type": "integer"}


def _obj(required: list[str], properties: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": required,
            "properties": properties}


SCHEMA_V1 = _obj(
    ["schema_version", "contributions", "fines", "officials", "approvals", "membership",
     "adoption", "statements", "kyc"],
    {
        "schema_version": {"const": SCHEMA_VERSION},
        "contributions": _obj(
            ["amount_cents", "frequency", "due_day", "grace_days", "allocation_order"],
            {
                "amount_cents": {**_int, "minimum": 100, "maximum": 10_000_000_000},
                # Weekly and fortnightly cycles come later; their periods need a new format.
                "frequency": {"enum": ["monthly"]},
                "due_day": {**_int, "minimum": 1, "maximum": 28},
                "grace_days": {**_int, "minimum": 0, "maximum": 14},
                "cash_banking_days": {**_int, "minimum": 0, "maximum": 14},
                "allocation_order": {
                    "type": "array", "minItems": 3, "maxItems": 3, "uniqueItems": True,
                    "items": {"enum": ["arrears", "fines", "current"]},
                },
            },
        ),
        "fines": _obj(["late_contribution"], {
            "stop_after_missed_months": {**_int, "minimum": 1, "maximum": 12},
            "late_contribution": _obj(["enabled", "amount_cents", "monthly_cap_cents"], {
                "enabled": {"type": "boolean"},
                "amount_cents": {**_int, "minimum": 0, "maximum": 1_000_000_000},
                "monthly_cap_cents": {"type": ["integer", "null"], "minimum": 0},
            }),
        }),
        "officials": _obj(["roles"], {
            "roles": {"type": "array", "minItems": 2, "uniqueItems": True,
                      "items": {"enum": ["chair", "treasurer", "secretary"]}},
        }),
        "approvals": _obj(["tiers", "deadline_hours"], {
            "tiers": {
                "type": "array", "minItems": 1, "maxItems": 4,
                "items": _obj(["up_to_cents", "officials", "member_vote"], {
                    "up_to_cents": {"type": ["integer", "null"], "minimum": 1},
                    "officials": {**_int, "minimum": 1},
                    "member_vote": {"type": "boolean"},
                }),
            },
            "deadline_hours": {**_int, "minimum": 12, "maximum": 336},
        }),
        "membership": _obj(["entry_fee_cents", "new_members_need_vote"], {
            "entry_fee_cents": {**_int, "minimum": 0, "maximum": 1_000_000_000},
            "new_members_need_vote": {"type": "boolean"},
        }),
        "adoption": _obj(["members_share_pct"], {
            "members_share_pct": {**_int, "minimum": 50, "maximum": 100},
        }),
        "statements": _obj(["every_days"], {
            "every_days": {**_int, "minimum": 7, "maximum": 31},
        }),
        "kyc": _obj(["payer"], {"payer": {"enum": ["chama", "member"]}}),
        # Leaving and death (5.13). Optional; defaults in exits.services.DEFAULTS.
        "exit": _obj(["notice_days", "deduction_pct", "death_benefit_cents"], {
            "notice_days": {**_int, "minimum": 0, "maximum": 180},
            # Protected: a leaving member keeps at least 90% of their savings.
            "deduction_pct": {**_int, "minimum": 0, "maximum": 10},
            "death_benefit_cents": {**_int, "minimum": 0, "maximum": 1_000_000_000},
        }),
        # Elections (5.12). Optional; defaults in elections.services.DEFAULTS.
        "elections": _obj(["term_months", "vote_days"], {
            "term_months": {**_int, "minimum": 6, "maximum": 60},
            "vote_days": {**_int, "minimum": 1, "maximum": 30},
        }),
        # Disputes (5.14). Optional; defaults in disputes.services.DEFAULTS.
        "disputes": _obj(["decided_by", "officials", "response_hours",
                          "allow_officials_only"], {
            "decided_by": {"enum": ["officials", "vote"]},
            "officials": {**_int, "minimum": 1, "maximum": 5},
            "response_hours": {**_int, "minimum": 24, "maximum": 336},
            "allow_officials_only": {"type": "boolean"},
        }),
        # Fine waivers (5.9). Optional; defaults in fines.waivers.DEFAULTS.
        "waivers": _obj(["officials", "member_vote"], {
            "officials": {**_int, "minimum": 1, "maximum": 5},
            "member_vote": {"type": "boolean"},
        }),
        # Loans (5.8). Optional; a chama without it has no loans. Behind the "loans" flag.
        "loans": _obj(["min_months", "savings_multiple_pct", "interest", "monthly_rate_bp",
                       "max_term_months", "guarantors", "grace_days", "late_penalty_cents",
                       "default_after_missed", "contribution_first"], {
            "min_months": {**_int, "minimum": 0, "maximum": 60},
            "savings_multiple_pct": {**_int, "minimum": 0, "maximum": 1000},
            "interest": {"enum": ["flat", "reducing"]},
            "monthly_rate_bp": {**_int, "minimum": 0, "maximum": 1000},
            "max_term_months": {**_int, "minimum": 1, "maximum": 60},
            "guarantors": {**_int, "minimum": 0, "maximum": 10},
            "grace_days": {**_int, "minimum": 0, "maximum": 30},
            "late_penalty_cents": {**_int, "minimum": 0, "maximum": 10_000_000},
            "default_after_missed": {**_int, "minimum": 1, "maximum": 12},
            "contribution_first": {"type": "boolean"},
        }),
        # Meetings (5.11). Optional; defaults in meetings.services.DEFAULTS.
        "meetings": _obj(["absence_fine_cents", "late_fine_cents", "late_after_minutes",
                          "apology_excuses", "objection_days"], {
            "absence_fine_cents": {**_int, "minimum": 0, "maximum": 10_000_000},
            "late_fine_cents": {**_int, "minimum": 0, "maximum": 10_000_000},
            "late_after_minutes": {**_int, "minimum": 0, "maximum": 180},
            "apology_excuses": {"type": "boolean"},
            "objection_days": {**_int, "minimum": 1, "maximum": 30},
        }),
        # Changing the rules (5.10). Optional; defaults in rules.proposals.DEFAULTS.
        "rule_changes": _obj(["notice_days", "vote_days", "quorum_pct", "pass_pct",
                              "cooling_days"], {
            "notice_days": {**_int, "minimum": 0, "maximum": 60},
            "vote_days": {**_int, "minimum": 1, "maximum": 30},
            "quorum_pct": {**_int, "minimum": 25, "maximum": 100},
            "pass_pct": {**_int, "minimum": 50, "maximum": 90},
            "cooling_days": {**_int, "minimum": 0, "maximum": 365},
        }),
        # Members' votes (5.11). Optional: missing means half must vote, more than half yes.
        "votes": _obj(["quorum_pct", "pass_pct", "secret"], {
            "quorum_pct": {**_int, "minimum": 10, "maximum": 100},
            "pass_pct": {**_int, "minimum": 50, "maximum": 90},
            "secret": {"type": "boolean"},
        }),
        # Merry-go-round turns (5.6). Optional: other chama types have no rotation.
        "rotation": _obj(["order", "arrears"], {
            "order": {"enum": ["fixed", "draw"]},
            "arrears": {"enum": ["skip", "deduct", "block"]},
        }),
    },
)
