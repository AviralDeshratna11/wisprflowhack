"""Authenticated encryption for evidence audio.

* At rest on the student machine: AES-256-GCM with a random local key that is
  itself protected by Windows DPAPI (CurrentUser scope). The DPAPI blob is never
  exported - it is not portable.
* For transfer to the examiner: each submission gets a fresh content key, wrapped
  for the examiner's X25519 public key (ephemeral ECDH + HKDF-SHA256 + AES-GCM).
  Only the examiner's private key file can open the audio.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

WRAP_INFO = b"swarlekhak-submission-key-v1"


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    nonce = os.urandom(12)
    return b"SWE1" + nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def decrypt(key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    if blob[:4] != b"SWE1":
        raise ValueError("not a SwarLekhak encrypted blob")
    return AESGCM(key).decrypt(blob[4:16], blob[16:], aad)


# ------------------------------------------------------------------ local key (DPAPI)
class LocalKeyStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._key: bytes | None = None

    def key(self) -> bytes:
        if self._key is None:
            if self.path.exists():
                self._key = _dpapi_unprotect(self.path.read_bytes())
            else:
                k = AESGCM.generate_key(bit_length=256)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_bytes(_dpapi_protect(k))
                os.replace(tmp, self.path)
                self._key = k
        return self._key


def _dpapi_protect(data: bytes) -> bytes:
    import win32crypt  # pywin32
    return win32crypt.CryptProtectData(data, "SwarLekhak local evidence key", None, None, None, 0)


def _dpapi_unprotect(blob: bytes) -> bytes:
    import win32crypt
    return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]


# ------------------------------------------------------------------ examiner recipient keys
def generate_examiner_keypair() -> tuple[bytes, str]:
    """Return (private key PEM bytes, public key base64 raw)."""
    priv = X25519PrivateKey.generate()
    pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return pem, b64(pub)


def load_private_key(pem: bytes) -> X25519PrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, X25519PrivateKey):
        raise ValueError("examiner key must be an X25519 private key")
    return key


def _kek(shared: bytes, eph_pub: bytes, recipient_pub: bytes) -> bytes:
    return HKDF(hashes.SHA256(), 32, salt=eph_pub + recipient_pub, info=WRAP_INFO).derive(shared)


def wrap_key(content_key: bytes, recipient_pub_b64: str) -> dict:
    recipient_pub = unb64(recipient_pub_b64)
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    shared = eph.exchange(X25519PublicKey.from_public_bytes(recipient_pub))
    kek = _kek(shared, eph_pub, recipient_pub)
    return {"alg": "X25519-HKDF-SHA256-AES256GCM", "ephemeral_public": b64(eph_pub),
            "recipient_public": recipient_pub_b64, "wrapped_key": b64(encrypt(kek, content_key, WRAP_INFO))}


def unwrap_key(wrapped: dict, private_key: X25519PrivateKey) -> bytes:
    eph_pub = unb64(wrapped["ephemeral_public"])
    my_pub = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if b64(my_pub) != wrapped["recipient_public"]:
        raise ValueError("This submission was encrypted for a different examiner key.")
    shared = private_key.exchange(X25519PublicKey.from_public_bytes(eph_pub))
    return decrypt(_kek(shared, eph_pub, my_pub), unb64(wrapped["wrapped_key"]), WRAP_INFO)


def dumps(obj) -> bytes:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
