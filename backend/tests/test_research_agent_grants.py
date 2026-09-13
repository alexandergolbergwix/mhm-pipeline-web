"""Tool-grant JWT: sign, expire, audience, wiki wrap isolation."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.services.research_agent.grants import (
    GRANT_TTL_SECONDS,
    issue_tool_grant,
    sign_tool_jwt,
    unwrap_wiki_token,
    verify_tool_jwt,
    wrap_wiki_token,
)
import uuid


def test_sign_and_verify_round_trip() -> None:
    gid = uuid.uuid4()
    token, expires = issue_tool_grant(
        grant_id=gid,
        thread_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        role="editor",
    )
    payload = verify_tool_jwt(token)
    assert payload["gid"] == str(gid)
    assert payload["aud"] == "research-agent-tools"
    assert payload["role"] == "editor"
    assert expires > datetime.now(timezone.utc)
    remaining = expires - datetime.now(timezone.utc)
    assert remaining <= timedelta(seconds=GRANT_TTL_SECONDS + 2)


def test_tampered_signature_is_rejected() -> None:
    token, _ = issue_tool_grant(
        grant_id=uuid.uuid4(),
        thread_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        run_id=None,
        role="viewer",
    )
    parts = token.split(".")
    bad = parts[0] + "." + parts[1] + "." + ("a" * len(parts[2]))
    with pytest.raises(HTTPException) as exc:
        verify_tool_jwt(bad)
    assert exc.value.status_code == 401


def test_wrong_audience_is_rejected() -> None:
    now = int(time.time())
    token = sign_tool_jwt({
        "gid": str(uuid.uuid4()),
        "tid": str(uuid.uuid4()),
        "uid": str(uuid.uuid4()),
        "pid": str(uuid.uuid4()),
        "rid": None,
        "role": "viewer",
        "aud": "someone-else",
        "iat": now,
        "exp": now + 60,
    })
    with pytest.raises(HTTPException) as exc:
        verify_tool_jwt(token)
    assert "audience" in str(exc.value.detail).lower()


def test_expired_token_is_rejected() -> None:
    now = int(time.time())
    token = sign_tool_jwt({
        "gid": str(uuid.uuid4()),
        "tid": str(uuid.uuid4()),
        "uid": str(uuid.uuid4()),
        "pid": str(uuid.uuid4()),
        "rid": None,
        "role": "viewer",
        "aud": "research-agent-tools",
        "iat": now - 120,
        "exp": now - 5,
    })
    with pytest.raises(HTTPException) as exc:
        verify_tool_jwt(token)
    assert "expired" in str(exc.value.detail).lower()


def test_wiki_token_never_enters_jwt() -> None:
    secret = "Alice@Bot:not-a-real-password"
    ct, nonce = wrap_wiki_token(secret)
    assert unwrap_wiki_token(ct, nonce) == secret
    token, _ = issue_tool_grant(
        grant_id=uuid.uuid4(),
        thread_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        run_id=None,
        role="editor",
    )
    assert "not-a-real-password" not in token
    assert "Alice@" not in token
