"""Load + validate the server-held crypto keys (``MASTER_KEY``, ``EMAIL_HMAC_KEY``).

Both must be 32 raw bytes. We accept urlsafe-base64 or hex in the env so
operators can paste whatever ``python -c "import secrets;print(secrets.token_urlsafe(32))"``
emitted.
"""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache

from app.settings import get_settings

_KEY_BYTES = 32


def _decode_key(raw: str, name: str) -> bytes:
    raw = (raw or "").strip()
    if not raw:
        raise RuntimeError(
            f"{name} is unset. Generate one with "
            f"'python -c \"import secrets;print(secrets.token_urlsafe(32))\"' "
            f"and add it to .env or Heroku config vars."
        )
    # Try urlsafe base64 first (the encoding `secrets.token_urlsafe` produces),
    # then hex, then plain base64. Pad ``=`` to make base64 forgiving.
    candidates: list[bytes] = []
    try:
        candidates.append(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except (binascii.Error, ValueError):
        pass
    try:
        candidates.append(bytes.fromhex(raw))
    except ValueError:
        pass
    try:
        candidates.append(base64.b64decode(raw + "=" * (-len(raw) % 4)))
    except (binascii.Error, ValueError):
        pass
    for candidate in candidates:
        if len(candidate) == _KEY_BYTES:
            return candidate
    raise RuntimeError(
        f"{name} could not be decoded as a 32-byte key (tried urlsafe-base64, "
        f"hex, base64). Got {len(raw)} input chars."
    )


@lru_cache(maxsize=1)
def master_key() -> bytes:
    return _decode_key(get_settings().master_key, "MASTER_KEY")


@lru_cache(maxsize=1)
def email_hmac_key() -> bytes:
    return _decode_key(get_settings().email_hmac_key, "EMAIL_HMAC_KEY")
