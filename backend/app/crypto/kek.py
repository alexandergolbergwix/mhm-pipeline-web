"""User-derived Key-Encryption Key (KEK) + session wrapping.

Zero-knowledge flow:

1. **Login** — derive the user's KEK from their password using Argon2id
   (slow, memory-hard) → :func:`derive_kek`. Argon2id parameters are
   tuned in :mod:`app.auth.password`.
2. **Session bootstrap** — generate a fresh random ``session_secret``,
   wrap the KEK with it, store the wrapped blob in
   ``sessions.kek_wrapped``. The plaintext KEK is forgotten on the
   server. The ``session_secret`` is sent to the browser as one half of
   an HTTP-only cookie.
3. **Per request** — cookie → ``session_secret`` → unwrap KEK from
   ``sessions.kek_wrapped`` via :func:`unwrap_kek`. KEK lives in memory
   only for the duration of the request handler.
4. **Logout / expiry** — session row deleted; the wrapped KEK is now
   unrecoverable.

This module owns only the wrap / unwrap step. Argon2id derivation is in
:mod:`app.auth.password` so the password-hashing parameters stay
co-located with the password-verification code.
"""

from __future__ import annotations

import os

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_KEK_BYTES = 32
_NONCE_BYTES = 12

# Argon2id parameters for KEK derivation. Independent from the password
# *verification* parameters in :mod:`app.auth.password` because the two
# operations have different security goals — KEK derivation needs to be
# deterministic given (password, salt), so we hold these constants
# stable; if we ever tune them up, we must re-derive every session's
# wrapped KEK on next login (a forward-only migration).
_ARGON2_TIME_COST = 3
_ARGON2_MEMORY_COST = 65536  # 64 MiB
_ARGON2_PARALLELISM = 4


def derive_kek(password: str, *, salt: bytes) -> bytes:
    """Argon2id(password, salt) → 32-byte KEK.

    *salt* is the per-user ``users.kek_salt`` value, generated once at
    user creation. The same (password, salt) pair always returns the
    same KEK so the same wrapped DEKs decrypt across logins.
    """
    if not password:
        raise ValueError("derive_kek(): empty password")
    if len(salt) < 16:
        raise ValueError("derive_kek(): salt must be at least 16 bytes")
    return hash_secret_raw(
        password.encode("utf-8"),
        salt,
        time_cost=_ARGON2_TIME_COST,
        memory_cost=_ARGON2_MEMORY_COST,
        parallelism=_ARGON2_PARALLELISM,
        hash_len=_KEK_BYTES,
        type=Type.ID,
    )


def new_session_secret() -> bytes:
    """32 random bytes for the wrap key of a single session."""
    return os.urandom(_KEK_BYTES)


def wrap_kek(kek: bytes, session_secret: bytes) -> tuple[bytes, bytes]:
    """AES-256-GCM(session_secret, KEK). Returns ``(ciphertext, nonce)``."""
    if len(kek) != _KEK_BYTES or len(session_secret) != _KEK_BYTES:
        raise ValueError("wrap_kek(): KEK and session_secret must be 32 bytes")
    nonce = os.urandom(_NONCE_BYTES)
    ct = AESGCM(session_secret).encrypt(nonce, kek, None)
    return ct, nonce


def unwrap_kek(wrapped: bytes, nonce: bytes, session_secret: bytes) -> bytes:
    """Inverse of :func:`wrap_kek`. Raises on tag/key mismatch."""
    if len(session_secret) != _KEK_BYTES:
        raise ValueError("unwrap_kek(): session_secret must be 32 bytes")
    return AESGCM(session_secret).decrypt(nonce, wrapped, None)
