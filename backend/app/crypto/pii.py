"""AES-256-GCM encryption for PII columns (server master key)."""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.crypto.keys import master_key

_NONCE_BYTES = 12
# Stored layout: nonce (12 bytes) || ciphertext+tag. Self-contained per
# column so we don't need a separate nonce column.


def encrypt_pii(plaintext: str) -> bytes:
    """Encrypt a UTF-8 string under the server master key."""
    if plaintext is None:
        raise ValueError("encrypt_pii() cannot encrypt None")
    nonce = os.urandom(_NONCE_BYTES)
    ct = AESGCM(master_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ct


def decrypt_pii(blob: bytes) -> str:
    """Inverse of :func:`encrypt_pii`."""
    if not blob or len(blob) <= _NONCE_BYTES:
        raise ValueError("decrypt_pii(): ciphertext too short")
    nonce, ct = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
    return AESGCM(master_key()).decrypt(nonce, ct, None).decode("utf-8")


def random_bytes(n: int) -> bytes:
    """Convenience wrapper for ``os.urandom`` so call-sites read intent."""
    return os.urandom(n)
