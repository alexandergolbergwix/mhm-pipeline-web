"""One-time token helpers (invitations + password resets).

Pattern: generate a random URL-safe token, return the *plaintext* to the
caller exactly once, persist only its SHA-256. Looking the token up at
acceptance time hashes the incoming value and matches by hash — a DB
leak alone never exposes a usable token.
"""

from __future__ import annotations

import hashlib
import secrets as stdlib_secrets


def new_token(byte_length: int = 32) -> tuple[str, bytes]:
    """Return ``(plaintext_token, sha256_hash)``."""
    plaintext = stdlib_secrets.token_urlsafe(byte_length)
    return plaintext, hash_token(plaintext)


def hash_token(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode("utf-8")).digest()
