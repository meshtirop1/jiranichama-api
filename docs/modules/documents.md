# documents

Statements as figures, as sealed PDFs, and the public check.
See [Member and chama statements](../flows/member-statements.md).

- `statements.py`: `member_statement` and `chama_statement` build the figures from the
  ledger (never from stored balances). Plain JSON with no words, versioned by
  `DATA_VERSION`; changing its shape changes every fingerprint, which is fine because old
  documents keep the figures they were made from in `IssuedDocument.data`.
- `labels.py`: the words, English and Kiswahili. A test keeps both languages' keys equal
  and covers every entry type a member statement lists.
- `render.py`: figures to PDF bytes with ReportLab, brand green #1B6B4A, QR code to the
  check page, fingerprint and page number in every footer.
- `services.py`: `issue` (reuse or render, then seal into the chain), `find` and `check`
  for the check page, `log`, and who may take a member's PDF.
- `models.py`: `IssuedDocument` (append-only; the PDF, its SHA-256, the fingerprint, the
  check code, the seal entry) and `DocumentAccess` (append-only log of view, issue,
  download and verify).
- `views.py`: the endpoints, plus `VerifyPageView` served at `/verify/{code}/` outside
  `/api/v1/`, because that is what the QR code opens.

Settings: `VERIFY_BASE_URL` (printed on every PDF and in the QR code) and
`VERIFY_THROTTLE_RATE`.
