# Nightly public anchoring (6.6)

Every chama's ledger is a hash chain, so any change to a past entry breaks every hash
after it. Anchoring goes further: it makes even a **wholesale rewrite** of a chain (by
us, or by anyone holding our database) detectable from outside.

## Every night at 00:30

1. **Leaves.** Each chama's chain head becomes a leaf: SHA-256 of the canonical JSON of
   `{chama_id, seq, hash}`. Leaves are sorted by chama.
2. **Root.** The leaves are hashed pairwise into one Merkle root. Each node is SHA-256 of
   its two children's bytes, left then right; an odd last node is paired with itself.
3. **Public timestamp.** The root is posted to the OpenTimestamps calendars
   (`OTS_CALENDARS`). The calendar's proof is kept, and within hours it is committed to
   Bitcoin, so anyone can later show the root existed that night.
4. **Off-site copy.** The root and every leaf are emailed to a mailbox outside our systems
   (`ANCHOR_EMAIL_TO`).
5. **Kept.** An `Anchor` row stores the root, leaves, proof and status. It is insert only;
   only the timestamp and email fields are filled in.

## Checking a chama

`GET /chamas/{id}/anchors/` gives, for the last 7 anchors that include the chama, the
chain head that night (seq and hash), its leaf, and the **Merkle path** to the root. The
app recomputes the leaf from the chain it holds and walks the path to the root, so a
member's phone can tell that its chain is the one anchored. A rewritten head no longer
fits the root.

## Settings

- `OTS_CALENDARS`: defaults to the a. and b. pool calendars; an empty list sends nothing.
- `ANCHOR_EMAIL_TO`: where the nightly copy goes. Set it before launch to a mailbox the
  owner controls outside the server.

## Not yet

Upgrading the calendar proofs to their Bitcoin attestations, and an object-locked copy in
storage (with the storage work).

## Trust (2026-10-20)

See [trusting the records](trust.md): the platform key chain, member countersignatures, the public proof file and the public lot for tied elections.
