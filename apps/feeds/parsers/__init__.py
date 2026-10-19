"""Bank statement parsers (6.9).

Phase 1 reads CSV with a header-detecting parser. Each bank has a profile of extra
column names and preamble labels. **No profile is verified against a real export yet**:
``verified=False`` until a pilot chama's real statement is added as a fixture with its
expected output. PDF statements are stored and sent to staff (``needs_parser``).
"""

from .base import NeedsParser, ParsedLine, ParsedStatement, StatementUnreadable, UnreadableLine
from .csv_parser import parse_csv
from .profiles import PROFILES, profile_for

__all__ = ["NeedsParser", "ParsedLine", "ParsedStatement", "StatementUnreadable",
           "UnreadableLine", "parse",
           "PROFILES", "profile_for"]


def parse(bank: str, filename: str, data: bytes) -> ParsedStatement:
    name = (filename or "").lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        raise NeedsParser("PDF statements are not read automatically yet.")
    if not name.endswith((".csv", ".txt")):
        raise NeedsParser("Upload the statement as a CSV file.")
    return parse_csv(data, profile_for(bank))
