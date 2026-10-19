"""Reading an Android key attestation (6.7).

When the phone makes its key, it asks the keystore for a certificate chain that vouches
for it. The leaf certificate holds the key's public key and a "key description"
extension (OID 1.3.6.1.4.1.11129.2.1.17) with the security level the key lives in and the
challenge we gave the phone. Each certificate is signed by the next; the last one is a
root.

We check: the leaf's key is the key being registered; every link in the chain is signed
by the next; the challenge is the nonce we issued; and whether the root is one we trust
(``ANDROID_ATTESTATION_ROOTS``: SHA-256 fingerprints of the roots' public keys, Google's
hardware attestation roots in production). Emulators attest in software with their own
root; such a key is recorded as ``software`` and untrusted, and production can refuse it
(``DEVICE_REQUIRE_TRUSTED_ATTESTATION``).
"""

import base64
import hashlib
from dataclasses import dataclass, field

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from django.conf import settings

KEY_DESCRIPTION_OID = x509.ObjectIdentifier("1.3.6.1.4.1.11129.2.1.17")
SECURITY = {0: "software", 1: "tee", 2: "strongbox"}


class AttestationError(ValueError):
    pass


@dataclass
class Attestation:
    security_level: str = "unknown"
    challenge: bytes = b""
    root_fingerprint: str = ""
    root_trusted: bool = False
    chain_subjects: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"security_level": self.security_level,
                "root_fingerprint": self.root_fingerprint,
                "root_trusted": self.root_trusted, "chain": self.chain_subjects}


def spki_fingerprint(spki_der: bytes) -> str:
    return hashlib.sha256(spki_der).hexdigest()


def _der_items(data: bytes, limit: int) -> list[tuple[int, bytes]]:
    """The first ``limit`` (tag, value) items inside a DER SEQUENCE."""
    def read_len(buf, i):
        first = buf[i]
        if first < 0x80:
            return first, i + 1
        n = first & 0x7F
        return int.from_bytes(buf[i + 1:i + 1 + n], "big"), i + 1 + n

    if not data or data[0] != 0x30:
        raise AttestationError("key description is not a sequence")
    length, i = read_len(data, 1)
    end, items = i + length, []
    while i < end and len(items) < limit:
        tag = data[i]
        n, j = read_len(data, i + 1)
        items.append((tag, data[j:j + n]))
        i = j + n
    return items


def _key_description(cert: x509.Certificate) -> tuple[str, bytes]:
    """(security level, challenge) from KeyDescription: attestationVersion,
    attestationSecurityLevel, keymasterVersion, keymasterSecurityLevel,
    attestationChallenge, ..."""
    try:
        ext = cert.extensions.get_extension_for_oid(KEY_DESCRIPTION_OID).value
    except x509.ExtensionNotFound as exc:
        raise AttestationError("the leaf certificate has no key description") from exc
    items = _der_items(ext.value, 5)
    if len(items) < 5:
        raise AttestationError("key description is too short")
    level = SECURITY.get(int.from_bytes(items[1][1], "big"), "unknown")
    return level, items[4][1]


def _signed_by(child: x509.Certificate, parent: x509.Certificate) -> bool:
    key = parent.public_key()
    try:
        if isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(child.signature, child.tbs_certificate_bytes,
                       ec.ECDSA(child.signature_hash_algorithm))
        elif isinstance(key, rsa.RSAPublicKey):
            key.verify(child.signature, child.tbs_certificate_bytes, padding.PKCS1v15(),
                       child.signature_hash_algorithm)
        else:
            return False
    except InvalidSignature:
        return False
    return True


def check_android(chain_b64: list[str], public_key_der: bytes) -> Attestation:
    if not chain_b64:
        raise AttestationError("no attestation chain")
    try:
        chain = [x509.load_der_x509_certificate(base64.b64decode(c)) for c in chain_b64]
    except ValueError as exc:
        raise AttestationError("the attestation chain is not readable") from exc
    leaf_spki = chain[0].public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if leaf_spki != public_key_der:
        raise AttestationError("the attestation is for a different key")
    for child, parent in zip(chain, chain[1:], strict=False):
        if not _signed_by(child, parent):
            raise AttestationError("the attestation chain is broken")
    level, challenge = _key_description(chain[0])
    root = chain[-1].public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    root_fp = spki_fingerprint(root)
    trusted = root_fp in {f.lower() for f in settings.ANDROID_ATTESTATION_ROOTS}
    return Attestation(security_level=level, challenge=challenge, root_fingerprint=root_fp,
                       root_trusted=trusted,
                       chain_subjects=[c.subject.rfc4514_string()[:120] for c in chain])


def verify_signature(public_key_der: bytes, message: bytes, signature_der: bytes) -> bool:
    """ECDSA P-256 with SHA-256 over ``message`` (what Android's SHA256withECDSA does)."""
    key = serialization.load_der_public_key(public_key_der)
    if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
        return False
    try:
        key.verify(signature_der, message, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return False
    return True
