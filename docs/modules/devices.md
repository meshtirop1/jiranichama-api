# devices

How people sign.

- `models.py`: `SigningChallenge` (SMS codes bound to a purpose, subject and content
  hash), `Device` (a phone's P-256 public key, its attestation and security level; only
  state and revoked time change), `DeviceNonce` (single-use, 5 minutes: attestation
  challenges and signing nonces).
- `signing.py`: issuing and confirming SMS codes.
- `attestation.py`: reading an Android key attestation chain (key description:
  security level and challenge; chain links; trusted root), and verifying ECDSA P-256
  signatures.
- `keys.py`: registering a key (SMS code + attestation), `active_device`, `revoke`, nonces.
- `views.py`: `/devices/`, `/devices/attest-nonce/`, `/devices/{id}/revoke/`.

See [Phone signing keys](../flows/device-keys.md).
