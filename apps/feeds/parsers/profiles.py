"""Per-bank hints for the CSV parser.

Every profile starts unverified. To verify one: add a real (anonymised) statement from
that bank under ``tests/fixtures/statements/<bank>/`` with its expected parsed output,
make the test pass, and set ``verified=True``. Until then the parser relies on common
column names and staff review the first statements from each bank.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Profile:
    bank: str
    verified: bool = False
    extra_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Day-first dates are the Kenyan convention; a profile can say otherwise.
    day_first: bool = True


PROFILES = {
    bank: Profile(bank=bank)
    for bank in ("equity", "kcb", "coop", "ncba", "absa", "stanbic", "family", "other")
}


def profile_for(bank: str) -> Profile:
    return PROFILES.get(bank, PROFILES["other"])
