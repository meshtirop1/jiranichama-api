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
    },
)
