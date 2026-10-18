"""The platform signing key.

The signed message is the 32-byte entry hash itself, signed as a pre-hashed SHA-256
digest with ECDSA P-256. That is what AWS KMS calls ``MessageType=DIGEST`` with
``ECDSA_SHA_256``, so local and KMS signatures verify the same way on the phone.
Signatures are DER-encoded.
"""

from functools import cache

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from django.conf import settings

_PREHASHED = ec.ECDSA(utils.Prehashed(hashes.SHA256()))


class LocalSigner:
    """Development and tests only: the private key comes from an environment variable."""

    def __init__(self, pem: str):
        if not pem:
            raise RuntimeError("PLATFORM_SIGNING_KEY_PEM is not set")
        self._key = serialization.load_pem_private_key(pem.encode(), password=None)
        if not isinstance(self._key, ec.EllipticCurvePrivateKey) or \
                not isinstance(self._key.curve, ec.SECP256R1):
            raise RuntimeError("platform signing key must be EC P-256")
        self.key_id = "local:" + _fingerprint(self._key.public_key())

    def sign_digest(self, digest: bytes) -> bytes:
        return self._key.sign(digest, _PREHASHED)

    def public_key(self) -> ec.EllipticCurvePublicKey:
        return self._key.public_key()


class KmsSigner:
    def __init__(self, key_id: str, region: str):
        import boto3

        if not key_id:
            raise RuntimeError("KMS_KEY_ID is not set")
        self._client = boto3.client("kms", region_name=region or None)
        self.key_id = key_id

    def sign_digest(self, digest: bytes) -> bytes:
        resp = self._client.sign(
            KeyId=self.key_id,
            Message=digest,
            MessageType="DIGEST",
            SigningAlgorithm="ECDSA_SHA_256",
        )
        return resp["Signature"]

    @cache  # noqa: B019 - one signer instance per process
    def public_key(self) -> ec.EllipticCurvePublicKey:
        der = self._client.get_public_key(KeyId=self.key_id)["PublicKey"]
        return serialization.load_der_public_key(der)


def _fingerprint(public_key) -> str:
    der = public_key.public_bytes(serialization.Encoding.DER,
                                  serialization.PublicFormat.SubjectPublicKeyInfo)
    digest = hashes.Hash(hashes.SHA256())
    digest.update(der)
    return digest.finalize().hex()[:16]


@cache
def get_signer():
    if settings.PLATFORM_SIGNER == "kms":
        return KmsSigner(settings.KMS_KEY_ID, settings.AWS_REGION)
    return LocalSigner(settings.PLATFORM_SIGNING_KEY_PEM)


def verify_digest(public_key, digest: bytes, signature: bytes) -> bool:
    try:
        public_key.verify(signature, digest, _PREHASHED)
    except InvalidSignature:
        return False
    return True


def public_key_pem(public_key) -> str:
    return public_key.public_bytes(serialization.Encoding.PEM,
                                   serialization.PublicFormat.SubjectPublicKeyInfo).decode()
