"""Login timing- and body-parity + rate-limit tests.

These tests pin three guarantees that together defeat the classic
account-enumeration attack on ``POST /api/auth/login``:

1. **Body parity** — the 401 response for "no such user" is
   byte-identical to the 401 for "wrong password". If the detail
   string ever diverges (e.g. someone adds a more specific message
   to one branch), an attacker can enumerate which emails have
   accounts just by reading the response body.

2. **Timing parity** — the no-such-user branch runs ``verify_password``
   against a module-level dummy hash so it burns roughly the same
   ~150 ms an Argon2id verify costs. The mean wall-clock of N runs
   on each side must agree to within 50 ms; anything wider re-opens
   the timing side-channel.

3. **Rate limit** — ``@limiter.limit("10/minute")`` is wired on
   ``/api/auth/login``. The 11th request from the same client IP
   within a minute must come back ``429 Too Many Requests`` so brute
   force / credential stuffing is throttled.

The router itself is at ``app/routers/auth.py``; the timing-parity
mechanism is the ``_DUMMY_HASH`` constant at module level. These
tests are the regression barrier — if either guarantee is weakened
or removed, the suite fails immediately.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession


_KNOWN_PASSWORD = "Correct-Horse-Battery-Staple-1!"
_WRONG_PASSWORD = "definitely-not-the-right-password"


def _reset_limiter() -> None:
    """Drop every in-process slowapi counter.

    The limiter is keyed by client IP and the test client always
    presents the same synthetic IP (``"testclient"`` via ``httpx``'s
    ``ASGITransport``), so a previous test that hit ``/api/auth/login``
    would steal counter budget from this one. Wiping the storage at the
    start of every test keeps them independent.

    Wrapped in try/except because slowapi's storage reset API has
    shifted between minor versions — the only invariant we rely on is
    that *some* path clears the in-memory counter.
    """
    from app.middleware.rate_limit import limiter

    try:
        limiter.reset()
        return
    except Exception:
        pass

    storage = getattr(limiter, "_storage", None)
    if storage is not None:
        for attr in ("reset", "clear"):
            fn = getattr(storage, attr, None)
            if callable(fn):
                try:
                    fn()
                    return
                except Exception:  # noqa: S110 — best-effort wipe
                    continue
        internal = getattr(storage, "storage", None)
        if isinstance(internal, dict):
            internal.clear()


async def _seed_user(db_session: "AsyncSession") -> str:
    """Create one user with ``_KNOWN_PASSWORD`` and return their email."""
    from app.auth import password as pw
    from app.crypto import index as idx
    from app.crypto import pii
    from app.models.user import ROLE_EDITOR, User

    email = f"existing+{uuid.uuid4().hex[:8]}@example.com"
    user = User(
        email_index=idx.blind_index(email),
        email_encrypted=pii.encrypt_pii(email),
        name_encrypted=pii.encrypt_pii("Existing User"),
        password_hash=pw.hash_password(_KNOWN_PASSWORD),
        kek_salt=pii.random_bytes(16),
        role=ROLE_EDITOR,
    )
    db_session.add(user)
    await db_session.commit()
    return email


@pytest.mark.asyncio
async def test_login_response_body_identical_for_missing_and_wrong_password(
    async_client: "AsyncClient",
    db_session: "AsyncSession",
) -> None:
    """The 401 body for wrong-password MUST equal the 401 body for no-such-user.

    Any divergence (different ``detail`` string, extra field, different
    field order in the JSON) gives an attacker a side-channel to
    enumerate which emails have accounts on the system. The router
    deliberately raises the same ``HTTPException(detail=...)`` on
    both branches; this test pins that.
    """
    _reset_limiter()
    existing_email = await _seed_user(db_session)
    missing_email = f"never-registered+{uuid.uuid4().hex[:8]}@example.com"

    wrong_resp = await async_client.post(
        "/api/auth/login",
        json={"email": existing_email, "password": _WRONG_PASSWORD},
    )
    missing_resp = await async_client.post(
        "/api/auth/login",
        json={"email": missing_email, "password": _WRONG_PASSWORD},
    )

    assert wrong_resp.status_code == 401, wrong_resp.text
    assert missing_resp.status_code == 401, missing_resp.text
    # Byte-identical body. ``response.content`` is the raw bytes the
    # server emitted, which is exactly what an attacker measures.
    assert wrong_resp.content == missing_resp.content, (
        f"login bodies diverge — enumeration risk: "
        f"wrong={wrong_resp.content!r} missing={missing_resp.content!r}"
    )


@pytest.mark.asyncio
async def test_login_response_time_parity_within_50ms(
    async_client: "AsyncClient",
    db_session: "AsyncSession",
) -> None:
    """Mean latency of the two 401 branches must agree to within 50 ms.

    The no-such-user branch runs ``verify_password`` against the
    module-level ``_DUMMY_HASH`` so both branches incur roughly the
    same Argon2id cost. If the dummy-hash defence is ever removed,
    no-such-user returns ~10 ms while wrong-password stays at
    ~150 ms — a >50 ms gap an attacker can measure remotely.

    Five calls per side is enough to wash out the per-call jitter
    on a CI runner without making the test painfully slow (≈1.5 s
    each side at typical Argon2id cost).
    """
    _reset_limiter()
    existing_email = await _seed_user(db_session)
    missing_email = f"never-registered+{uuid.uuid4().hex[:8]}@example.com"

    samples = 5

    async def _avg(email: str) -> float:
        durations: list[float] = []
        for _ in range(samples):
            _reset_limiter()  # keep every call inside the 10/min budget
            t0 = time.perf_counter()
            resp = await async_client.post(
                "/api/auth/login",
                json={"email": email, "password": _WRONG_PASSWORD},
            )
            durations.append(time.perf_counter() - t0)
            assert resp.status_code == 401, resp.text
        return sum(durations) / len(durations)

    avg_wrong = await _avg(existing_email)
    avg_missing = await _avg(missing_email)

    assert abs(avg_missing - avg_wrong) < 0.05, (
        f"login timing diverges — enumeration risk: "
        f"avg_wrong={avg_wrong * 1000:.1f}ms avg_missing={avg_missing * 1000:.1f}ms "
        f"delta={abs(avg_missing - avg_wrong) * 1000:.1f}ms"
    )


@pytest.mark.asyncio
async def test_login_rate_limit_429(
    async_client: "AsyncClient",
    db_session: "AsyncSession",
) -> None:
    """The 11th rapid login from the same client IP MUST be 429.

    ``@limiter.limit("10/minute")`` is configured on the route; the
    first ten requests within a sliding 60-second window are processed
    (and 401 because the password is wrong); the 11th hits the limit
    and slowapi short-circuits with 429 before the handler runs.

    Resetting the limiter at the top guarantees we start the test
    inside a fresh quota window — otherwise a previous test's calls
    would have eaten part of the budget on the same synthetic client
    IP.
    """
    _reset_limiter()
    existing_email = await _seed_user(db_session)

    statuses: list[int] = []
    for _ in range(11):
        resp = await async_client.post(
            "/api/auth/login",
            json={"email": existing_email, "password": _WRONG_PASSWORD},
        )
        statuses.append(resp.status_code)

    # The first ten are processed (401 wrong-password); the 11th hits
    # the limit and short-circuits.
    assert all(s == 401 for s in statuses[:10]), (
        f"expected first 10 to be 401, got {statuses[:10]}"
    )
    assert statuses[10] == 429, (
        f"expected 11th request to be rate-limited, got {statuses[10]} "
        f"(full sequence: {statuses})"
    )
