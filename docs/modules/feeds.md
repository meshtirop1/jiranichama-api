# feeds

Where bank data comes in. See [Bank statements](../flows/statements.md).

| Piece | What it does |
| --- | --- |
| `Statement` | One statement file: route, who uploaded and confirmed it, mail checks, parsed lines, problems and flags. Written once; only `state`, `confirmed_by`, `confirmed_at` change |
| `BankTransaction` | One bank line from any feed. Written once; only `state` changes. Narration is stored encrypted |
| `parsers/` | CSV parser and per-bank profiles (all unverified until real fixtures exist) |
| `checks.py` | The checks that refuse or flag a statement |
| `services.py` | Upload, second-official confirmation, the statement inbox, ingesting lines |

Settings: `STATEMENT_MAX_BYTES`, `STATEMENT_INBOX_DOMAIN`, `MAIL_HOOK_TOKEN`,
`MAIL_HOOK_ALLOWED_IPS`, `BANK_STATEMENT_SENDERS` (empty by default, so mail is held).

Files are stored with Django's storage API: local disk in development, the private
object-locked bucket in production.

## Typed-in lines

`TypedLine`: a statement row an official typed in because the parser could not read it.
Written once, all of a statement's rows together, only after the statement adds up
(`services.submit_typed_lines`). `services.all_lines` gives read and typed rows together
in file order; ingest and the confirmation code use it. `BankTransaction.typed_by` marks
lines that came from typing. See [Bank statements](../flows/statements.md).
