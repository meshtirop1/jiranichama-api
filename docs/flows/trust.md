# Trusting the records: key chain, member countersignatures, public proof (2026-10-20)

The owner asked to "remember the cryptographic part". Before this change, the records were
already hash-chained, signed by the platform and anchored every night. This change closes
three gaps: a stolen server swapping the signing key, the platform rewriting history
before anyone anchored it, and anchors nobody outside could check.

## 1. The platform key chain

- `GET /platform-keys/` is public. It lists every key that has ever signed records, in
  order (`ledger.PlatformKey`, insert only).
- The first key is the one the app is built to trust. A release build passes its
  fingerprint (SHA-256 of the DER) as `PLATFORM_KEY_FP`. A dev build pins the first key
  it sees and remembers it.
- Every later key carries an **endorsement**: the previous key's signature (ECDSA P-256,
  pre-hashed SHA-256) over the canonical JSON
  `{"v":1,"purpose":"platform_key_endorsement","key_id":…,"fingerprint":…,"prev_key_id":…}`.
- The phone walks the chain from its pinned key and checks each step:
  - the fingerprint really is the key's own;
  - the previous key endorsed it.

  A record signed by any key that is not on the verified chain is a problem: "signed by
  a key this phone does not trust".
- The server refuses to sign with a key that is not on the chain (`trust.ensure_current_key`
  in `sign_entry`).
- **Rotating** the key:
  1. Run `manage.py endorse_platform_key --key-id … --public-pem new.pem` while the old
     key is still the signer.
  2. Switch the signer to the new key.

  Without the old private key (held in KMS in production), nobody can make a new key
  that phones accept.

## 2. Members countersign the chain

- After a phone has checked the whole chain, the member can tap **Vouch for these
  records**. The phone signs the canonical JSON
  `{"v":1,"purpose":"countersign_chain","chama_id":…,"member_code":…,"seq":…,"hash":…}`
  with the key in its secure chip, after a fingerprint, face or PIN.
- `POST /chamas/{id}/countersignatures/` accepts the signature only if all of these hold:
  - it is from the member's active phone key;
  - the signature verifies;
  - `seq` and `hash` are this chama's record at that point. A different hash is
    `head_mismatch`: that phone saw a different history.
- `GET /chamas/{id}/countersignatures/` returns each member's latest countersignature,
  with the phone's public key. **Every phone checks every one** against its own chain.
  If anyone's signed hash differs from what this phone sees, the Records tab shows a red
  warning naming them.
- Once members have signed a point in the history, replacing it would need every one of
  those members' phones. Not even JiraniChama can do it. This is stored in
  `ledger.HeadCountersignature` (insert only, one per member per point).

## 3. The public proof anyone can check

- `GET /anchors/{id}/ots/` (public) returns the night's OpenTimestamps proof as a
  standard `.ots` file, with the Merkle root in `X-Merkle-Root`.
- In the app, **Share the Bitcoin timestamp proof** sends the file. Anyone can check it
  with the free `ots verify` tool, which needs Bitcoin and nothing from JiraniChama.

## 4. A tied election, settled in public

1. A tied election vote (as many yes as no) is held again once, with the question
   "Tied. Vote again: …".
2. A second tie puts the election in `awaiting_lot`.
3. Right after the next nightly anchor, `elections.draw_lots` takes
   `SHA-256("{merkle_root}:{election_id}")`. An even last hex digit elects the candidate;
   an odd one does not.
4. The draw, the root and the anchor go on the ledger as a `vote_result` with
   `"lot": true`, so anyone can recompute it. Nobody can know or steer the root in
   advance: it covers every chama's chain that night.

## Shared vectors

`vectors/key_chain.json` holds a two-key chain, a forged third key and one member
countersignature, all with test keys. The server (`tests/test_trust.py`) and the app
(`jirani_domain/test/key_chain_test.dart`) both verify it, so the two implementations
cannot drift apart.

## Not yet

- Checking the Bitcoin attestation inside the phone itself; today the phone hands the
  proof to `ots verify`.
- Ballot signatures and SMS approvals on the ledger.
- An ID-number hash for one person, one membership (with KYC).
