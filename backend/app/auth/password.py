"""Argon2id password hashing (verify-only path).

Distinct from the KEK derivation in :mod:`app.crypto.kek`: this is for
the *verification* path (``password == stored_hash?``), while
:func:`app.crypto.kek.derive_kek` deterministically derives the wrapping
key from the password. We deliberately use the high-level
``PasswordHasher`` API here so future parameter tuning rotates hashes on
the next successful login without needing a manual migration.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("hash_password(): empty password")
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    if not password or not stored_hash:
        return False
    try:
        return _hasher.verify(stored_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:  # noqa: BLE001 — defensive: malformed hash etc.
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when the stored hash should be replaced after a successful
    login (e.g. we've tuned the parameters since the hash was written)."""
    return _hasher.check_needs_rehash(stored_hash)
