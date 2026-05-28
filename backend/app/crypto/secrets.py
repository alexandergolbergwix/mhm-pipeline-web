"""Envelope encryption for user-stored API keys (Gemini, Wikidata, …).

Each saved secret has its own random Data Encryption Key (DEK). The DEK
encrypts the secret value via AES-256-GCM; the DEK itself is then wrapped
with the *user's* KEK (see :mod:`app.crypto.kek`). All four byte strings
(``ciphertext``, ``ciphertext_nonce``, ``dek_wrapped``, ``dek_wrap_nonce``)
get persisted to ``api_keys`` columns of the same name.

Because the KEK is derived from the user's password and never stored
plaintext on the server, even a complete DB + binary dump leaks zero
plaintext secrets — that is the zero-knowledge property.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_DEK_BYTES = 32
_NONCE_BYTES = 12


@dataclass(frozen=True)
class WrappedSecret:
    ciphertext: bytes
    ciphertext_nonce: bytes
    dek_wrapped: bytes
    dek_wrap_nonce: bytes


def wrap_secret(plaintext: str, *, kek: bytes) -> WrappedSecret:
    """Encrypt *plaintext* under a fresh DEK; wrap the DEK with *kek*."""
    if not plaintext:
        raise ValueError("wrap_secret(): empty plaintext")
    if len(kek) != _DEK_BYTES:
        raise ValueError("wrap_secret(): kek must be 32 bytes")

    dek = os.urandom(_DEK_BYTES)
    ct_nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(dek).encrypt(ct_nonce, plaintext.encode("utf-8"), None)

    dek_nonce = os.urandom(_NONCE_BYTES)
    dek_wrapped = AESGCM(kek).encrypt(dek_nonce, dek, None)

    return WrappedSecret(ciphertext, ct_nonce, dek_wrapped, dek_nonce)


def unwrap_secret(blob: WrappedSecret, *, kek: bytes) -> str:
    """Inverse of :func:`wrap_secret`."""
    if len(kek) != _DEK_BYTES:
        raise ValueError("unwrap_secret(): kek must be 32 bytes")
    dek = AESGCM(kek).decrypt(blob.dek_wrap_nonce, blob.dek_wrapped, None)
    plaintext = AESGCM(dek).decrypt(blob.ciphertext_nonce, blob.ciphertext, None)
    return plaintext.decode("utf-8")
