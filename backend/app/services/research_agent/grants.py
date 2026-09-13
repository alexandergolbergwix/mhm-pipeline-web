"""Short-lived HS256 tool grants for the Modal research agent.

The JWT carries grant/thread/project ids only. A Wikidata bot password is
re-wrapped with MASTER_KEY onto the grant row and never enters the JWT or
a Modal secret.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, status

from app.crypto.keys import master_key

GRANT_TTL_SECONDS = 15 * 60
_JWT_AUD = "research-agent-tools"
_NONCE_BYTES = 12


def normalize_reply_text(text: str) -> str:
    """Canonical form for reply-cache keys: collapse whitespace, strip."""
    return " ".join((text or "").split())


def reply_text_hash(text: str) -> str:
    return hashlib.sha256(normalize_reply_text(text).encode("utf-8")).hexdigest()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def jwt_signing_key() -> bytes:
    """Domain-separated HMAC key derived from MASTER_KEY."""
    return hashlib.sha256(master_key() + b"|research-agent-tools").digest()


def sign_tool_jwt(payload: dict[str, Any], *, key: bytes | None = None) -> str:
    key = key or jwt_signing_key()
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(key, f"{header}.{body}".encode("ascii"), hashlib.sha256).digest()
    return f"{header}.{body}.{_b64url(sig)}"


def verify_tool_jwt(token: str, *, key: bytes | None = None) -> dict[str, Any]:
    key = key or jwt_signing_key()
    try:
        header_b64, body_b64, sig_b64 = token.split(".")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed tool grant.") from exc
    expected = hmac.new(key, f"{header_b64}.{body_b64}".encode("ascii"), hashlib.sha256).digest()
    actual = _b64url_decode(sig_b64)
    if not hmac.compare_digest(expected, actual):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid tool grant signature.")
    try:
        payload = json.loads(_b64url_decode(body_b64))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed tool grant payload.") from exc
    if payload.get("aud") != _JWT_AUD:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Tool grant audience mismatch.")
    exp = payload.get("exp")
    now = int(datetime.now(timezone.utc).timestamp())
    if not isinstance(exp, int) or exp < now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Tool grant expired.")
    return payload


@dataclass(frozen=True)
class GrantClaims:
    grant_id: uuid.UUID
    thread_id: uuid.UUID
    user_id: uuid.UUID
    project_id: uuid.UUID
    run_id: uuid.UUID | None
    role: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> GrantClaims:
        try:
            run_raw = payload.get("rid")
            return cls(
                grant_id=uuid.UUID(str(payload["gid"])),
                thread_id=uuid.UUID(str(payload["tid"])),
                user_id=uuid.UUID(str(payload["uid"])),
                project_id=uuid.UUID(str(payload["pid"])),
                run_id=uuid.UUID(str(run_raw)) if run_raw else None,
                role=str(payload.get("role") or "viewer"),
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Tool grant is missing required claims.",
            ) from exc


def issue_tool_grant(
    *,
    grant_id: uuid.UUID,
    thread_id: uuid.UUID,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    run_id: uuid.UUID | None,
    role: str,
    ttl_seconds: int = GRANT_TTL_SECONDS,
) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=ttl_seconds)
    payload = {
        "gid": str(grant_id),
        "tid": str(thread_id),
        "uid": str(user_id),
        "pid": str(project_id),
        "rid": str(run_id) if run_id else None,
        "role": role,
        "aud": _JWT_AUD,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return sign_tool_jwt(payload), expires


def wrap_wiki_token(plaintext: str) -> tuple[bytes, bytes]:
    nonce = __import__("os").urandom(_NONCE_BYTES)
    ct = AESGCM(master_key()).encrypt(nonce, plaintext.encode("utf-8"), b"research-agent-wiki")
    return ct, nonce


def unwrap_wiki_token(ciphertext: bytes, nonce: bytes) -> str:
    return AESGCM(master_key()).decrypt(nonce, ciphertext, b"research-agent-wiki").decode("utf-8")
