# Member and chama statements (4.8)

A statement is the chama's figures for one member, or for the whole chama, over a period.
Members read it in the app; anyone can take a PDF of it to a bank, a lender or the AGM,
and whoever receives the PDF can check it is genuine without an account.

## Reading the figures in the app

- `GET /memberships/{id}/statement/?from=&to=`: one member. Every member may see every
  member's figures, as with positions (the records are open).
- `GET /chamas/{id}/statement/?from=&to=`: every current member's totals, plus the bank
  side: the last accepted bank statement and its closing balance, payments not yet
  matched, and cash members confirmed that is not yet banked.

`from` defaults to when the member (or the first member) joined; `to` defaults to today
and may not be later. Dates are the day the money moved: the bank's value date for
payments, the day after grace for a late fine, otherwise the day the entry was written.
The **position** part is always as of today.

Every look is logged (`DocumentAccess`, action `view`).

## Making the PDF

`POST /memberships/{id}/statement/pdf/` or `POST /chamas/{id}/statement/pdf/` with
optional `period_from`, `period_to` and `language` (`en` or `sw`; default the user's
language). Needs an `Idempotency-Key`.

1. **Figures.** The same JSON as above. Its canonical JSON (RFC 8785) hashed with SHA-256
   is the statement's **fingerprint**. The figures contain no words, so the English and
   Kiswahili PDFs share a fingerprint.
2. **Already made?** If a PDF with exactly these figures, language and subject exists, we
   hand that one back. Nothing new is written, so tapping "Download" twice does not fill
   the chain with copies. The figures say "records up to entry #N" with seals left out,
   so making a statement does not itself change the next statement.
3. **Render.** ReportLab draws the PDF: chama, member, period, summary, position and every
   line (reversed lines struck through). Each page's footer has the fingerprint, the
   10-character check code and the check address; the first page has a QR code to it.
4. **Seal.** In one transaction we write a `seal` entry into the chama's hash chain
   (decision: kind, check code, fingerprint, the PDF's SHA-256, period) and store the PDF
   in private storage. `IssuedDocument` rows are append-only in the database.

A member statement PDF is for **the member, an official or the auditor** only. Other
members can read the figures in the app but not take a named PDF of someone else.
`GET /documents/{id}/file/` downloads it (logged as `download`).

## Checking a statement

- The QR code opens `/verify/{code}/`, a plain page saying **Genuine** (or not), with
  the chama, member code, period, date and fingerprint. The fingerprint must match the
  one printed on the paper.
- `GET /api/v1/verify/{code}/` gives the same as JSON. `POST` it with the PDF as `file`
  and the answer adds `file_matches`: whether those are exactly the bytes we issued.
- Codes use Crockford Base32: case, dashes and spaces do not matter, and O/I/L read as
  0/1/1, so a code read out over the phone still works.
- **Genuine** means the seal entry still hashes to itself in the chain and names this
  file and these figures. Someone who edits the PDF changes its bytes; someone who edits
  our database row breaks the seal.
- The check never shows a member's name or phone: only what the statement holder already
  has. No sign-in; limited to `VERIFY_THROTTLE_RATE` (default 30 a minute) per address.
  Every check is logged (`verify`, no user).

## Not yet

- Monthly statements made automatically on the 1st, and the SMS summary (Phase 2, with
  the notification work).
- Production storage moves PDFs to the private bucket behind short-lived links (6.6);
  today they are files in `MEDIA_ROOT` served through the API after a membership check.
- The design named WeasyPrint. We use ReportLab instead: pure Python, so the server and CI
  need no system libraries (Pango, Cairo), and it handles the two-language layouts.
