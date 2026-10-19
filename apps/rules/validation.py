"""Checking a set of rules before it can be adopted (6.8).

The JSON Schema catches shape and ranges. The cross-field checks catch contradictions
the schema cannot express, and enforce the platform rules no chama can switch off.
"""

from dataclasses import dataclass

from jsonschema import Draft202012Validator

from .schema import SCHEMA_V1

_validator = Draft202012Validator(SCHEMA_V1)


@dataclass
class RuleError:
    """``code`` is stable, so the app can say it in the member's language; ``message`` is
    the English explanation."""

    path: str
    message: str
    code: str = "out_of_range"

    def as_dict(self) -> dict:
        return {"path": self.path, "code": self.code, "message": self.message}


class RulesInvalid(ValueError):
    def __init__(self, errors: list[RuleError]):
        super().__init__("; ".join(f"{e.path}: {e.message}" for e in errors))
        self.errors = errors


def check(rules: dict) -> list[RuleError]:
    errors = [
        RuleError("/".join(str(p) for p in e.absolute_path) or "(root)", e.message)
        for e in sorted(_validator.iter_errors(rules), key=lambda e: list(e.absolute_path))
    ]
    if errors:
        return errors
    return _cross_field(rules)


def validate(rules: dict) -> dict:
    errors = check(rules)
    if errors:
        raise RulesInvalid(errors)
    return rules


def _cross_field(rules: dict) -> list[RuleError]:
    errors = []
    officials = len(rules["officials"]["roles"])
    tiers = rules["approvals"]["tiers"]

    if tiers[-1]["up_to_cents"] is not None:
        errors.append(RuleError("approvals/tiers",
                                "The last tier must cover any amount (no upper limit).",
                                "tiers_last_unlimited"))
    if any(t["up_to_cents"] is None for t in tiers[:-1]):
        errors.append(RuleError("approvals/tiers",
                                "Only the last tier can be without an upper limit.",
                                "tiers_only_last_unlimited"))
    limits = [t["up_to_cents"] for t in tiers if t["up_to_cents"] is not None]
    if limits != sorted(limits) or len(set(limits)) != len(limits):
        errors.append(RuleError("approvals/tiers", "Tier limits must go up in order.",
                                "tiers_order"))

    counts = [t["officials"] for t in tiers]
    if counts != sorted(counts):
        errors.append(RuleError("approvals/tiers",
                                "A larger amount cannot need fewer officials.",
                                "tiers_officials_order"))
    for i, tier in enumerate(tiers):
        # Platform rule: the person paid never approves, so a payout to any one official
        # must still be approvable by the others (proposal v1.1, 6.8).
        if tier["officials"] > officials - 1:
            errors.append(RuleError(
                f"approvals/tiers/{i}/officials",
                f"With {officials} officials, at most {officials - 1} can be required, "
                "because nobody approves a payout to themselves.",
                "tiers_too_many_officials",
            ))

    waiver = rules.get("waivers")
    if waiver and waiver["officials"] > officials - 1:
        # The same platform rule as P.1: an official's own fine is decided by the others.
        errors.append(RuleError(
            "waivers/officials",
            f"With {officials} officials, at most {officials - 1} can be required, because "
            "nobody decides a waiver of their own fine.", "waivers_too_many_officials"))

    dispute = rules.get("disputes")
    if dispute and dispute["decided_by"] == "officials" and \
            dispute["officials"] > officials - 1:
        errors.append(RuleError(
            "disputes/officials",
            f"With {officials} officials, at most {officials - 1} can be required, because "
            "an official involved in a dispute does not decide it.",
            "disputes_too_many_officials"))

    fine = rules["fines"]["late_contribution"]
    cap = fine["monthly_cap_cents"]
    if fine["enabled"] and fine["amount_cents"] == 0:
        errors.append(RuleError("fines/late_contribution/amount_cents",
                                "A late fine that is switched on needs an amount.",
                                "fine_needs_amount"))
    if cap is not None and cap < fine["amount_cents"]:
        errors.append(RuleError("fines/late_contribution/monthly_cap_cents",
                                "The monthly cap cannot be lower than one fine.",
                                "fine_cap_below_fine"))
    return errors
