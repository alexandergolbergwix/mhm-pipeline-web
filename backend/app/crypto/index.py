"""HMAC blind index for searchable encrypted columns (e.g. user.email).

Deterministic ``HMAC-SHA256(EMAIL_HMAC_KEY, value.lower().strip())`` produces
a fixed-length opaque token a unique index can sit on. An attacker with the
DB but not the HMAC key cannot tell which row is which user; an attacker
with the HMAC key cannot decrypt the actual email (that takes the
:data:`MASTER_KEY`).
"""

from __future__ import annotations

import hmac
from hashlib import sha256

from app.crypto.keys import email_hmac_key


def blind_index(value: str) -> bytes:
    """Return the deterministic blind index for *value*.

    Case- and whitespace-insensitive: ``"Alice@x.io"`` and ``" alice@x.io "``
    collapse to the same index so login looks up the user regardless of
    typing quirks.
    """
    norm = (value or "").strip().lower().encode("utf-8")
    return hmac.new(email_hmac_key(), norm, sha256).digest()
