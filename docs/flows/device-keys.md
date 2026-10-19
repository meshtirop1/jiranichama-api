# Phone signing keys (6.7)

Officials who have the app approve money out with their **fingerprint, face or phone
PIN**. Their phone's secure chip holds a key that never leaves it. Officials without the
app keep the SMS code.

## Setting up a phone

1. The app asks for a challenge (`POST /devices/attest-nonce/`, single use, 5 minutes).
2. The phone makes a P-256 key in its keystore. The key can sign only after a
   fingerprint, face or PIN (Android: `setUserAuthenticationRequired`, StrongBox where the
   phone has one). The phone puts the challenge in the key's **attestation**: a
   certificate chain in which the phone's keystore vouches for the key.
3. `POST /devices/` with the public key (SPKI), platform, a label and the chain sends an
   SMS code; the same call with the code registers it. The code proves the account owner
   holds this phone.
4. The server checks the attestation:
   - the leaf certificate's key is the key being registered;
   - every certificate is signed by the next;
   - the challenge is the one it issued;
   - the security level (strongbox, tee, software) from Android's key description;
   - whether the root is trusted (`ANDROID_ATTESTATION_ROOTS`: SHA-256 of the roots'
     public keys).
5. Every chama where the person is an official gets a `device_registered` ledger entry,
   so every member can see that this official now signs with a phone.

Emulators attest in software with their own root: they register as `software`,
untrusted. **Before money out goes live**, put Google's hardware attestation root
fingerprints in `ANDROID_ATTESTATION_ROOTS` and set `DEVICE_REQUIRE_TRUSTED_ATTESTATION=1`.
Then only keys vouched for by real phone hardware register.

`POST /devices/{id}/revoke/` retires a key (a lost phone); the official is back on SMS
codes.

## Approving with the key

1. `POST /proposals/{id}/signing-payload/` returns the payload and a single-use nonce:
   chama, proposal, kind, amount (and any deduction), payee, payee account, purpose, rule
   version.
2. The app shows the payload and asks for the fingerprint, face or PIN. The phone signs
   the canonical JSON of the payload with ECDSA P-256 / SHA-256.
3. `POST /proposals/{id}/signatures/` with the device, nonce, payload hash and signature.
   The server rebuilds the payload from the proposal as it is **now**:
   - a changed payload gets `409 payload_changed`;
   - a reused nonce is refused;
   - a signature that does not verify, or a key that is not the official's own active
     key, is refused.
4. The approval records the device, the payload hash and the signature.

Once an official has an active key, **SMS approval is refused for them**
(`use_device_key`), so a stolen SMS cannot stand in for their fingerprint.

When the payout is matched, its ledger entry lists each approval's method (device or
sms). The phone signatures are bound into the entry's hash through
`signatures_digest` (member code, key fingerprint, signature).

## Not yet

iOS (Secure Enclave and App Attest; the app falls back to SMS on iPhone for now), signing
ballots (votes arrive later), and checking attestation certificate revocation lists.
