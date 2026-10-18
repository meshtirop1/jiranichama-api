from dataclasses import asdict, dataclass, field
from datetime import date


class StatementUnreadable(ValueError):
    """The file is a statement we should be able to read, but parts of it make no sense."""

    def __init__(self, message: str, lines: list[int] | None = None):
        super().__init__(message)
        self.lines = lines or []


class NeedsParser(ValueError):
    """We do not read this format yet; staff build a parser (with the chama's consent)."""


@dataclass
class ParsedLine:
    line_no: int
    value_date: date
    narration: str
    reference: str
    credit_cents: int
    debit_cents: int
    balance_cents: int | None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["value_date"] = self.value_date.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ParsedLine":
        return cls(**{**d, "value_date": date.fromisoformat(d["value_date"])})


@dataclass
class ParsedStatement:
    parser: str
    account_no: str | None
    period_from: date
    period_to: date
    opening_cents: int
    closing_cents: int
    lines: list[ParsedLine] = field(default_factory=list)

    @property
    def credits_cents(self) -> int:
        return sum(line.credit_cents for line in self.lines)

    @property
    def debits_cents(self) -> int:
        return sum(line.debit_cents for line in self.lines)
