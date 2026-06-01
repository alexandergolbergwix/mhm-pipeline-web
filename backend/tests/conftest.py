"""Pytest fixtures for the MHM Pipeline web backend.

Three pieces this conftest sets up:

* **Crypto env seed** — ensures ``MASTER_KEY`` + ``EMAIL_HMAC_KEY`` are
  present before any module that ``from app.settings import …`` runs.
  Tests get fresh random keys each session; the production env vars
  win when set (e.g. on CI matrix runs that pin them).

* **In-memory SQLite engine** — replaces the production async-Postgres
  engine with ``sqlite+aiosqlite:///:memory:`` for the duration of the
  test session. The Postgres-specific column types (``JSONB`` and
  ``UUID(as_uuid=True)``) are taught to compile down to ``JSON`` and
  ``CHAR(36)`` on SQLite so the production ORM models work unchanged.

* **HTTP + DB fixtures** — ``async_client`` wraps the FastAPI app in
  ``httpx.AsyncClient`` with ``ASGITransport``; ``db_session`` yields
  a single ``AsyncSession`` (rolled back at teardown); ``auth_user``
  creates a user + minted session and returns an authenticated client;
  ``sample_run`` seeds a project + run scaffold so tests that need
  scope IDs don't have to repeat themselves.

Fixtures are deliberately session-scoped where they can be (engine,
schema) and function-scoped where isolation matters (db_session,
async_client). Each test gets a fresh transaction that rolls back at
teardown, so cross-test bleed is impossible.
"""

from __future__ import annotations

import os
import secrets
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest_asyncio


# ── Step 1 — env seeding (must run BEFORE any app.* import) ────────────


def _seed_test_env() -> None:
    os.environ.setdefault("MASTER_KEY", secrets.token_urlsafe(32))
    os.environ.setdefault("EMAIL_HMAC_KEY", secrets.token_urlsafe(32))
    os.environ.setdefault("ENV", "test")
    # Point the app at an in-memory SQLite — the engine swap below
    # honours this so the production code path is unchanged.
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    # Skip the Postgres LISTEN bridge — it crashes against SQLite.
    os.environ.setdefault("DISABLE_PG_LISTENER", "1")
    # Default admin notification recipient so access-request confirm
    # tests fire the email path without needing a fixture-created admin
    # user. Tests can override per-case via monkeypatch.
    os.environ.setdefault("ADMIN_NOTIFICATION_EMAIL", "admin@test.local")


_seed_test_env()


# ── Step 2 — teach Postgres-only types to compile on SQLite ────────────


def _patch_pg_types_for_sqlite() -> None:
    """JSONB → JSON and UUID(as_uuid=True) → CHAR(36) on the sqlite dialect.

    The production ORM uses ``sqlalchemy.dialects.postgresql.JSONB`` and
    ``UUID(as_uuid=True)`` directly. On SQLite both raise
    ``CompileError`` at table-create time. Registering compile rules
    on the SQLite dialect lets the same metadata create tables on
    either backend.
    """
    from sqlalchemy.dialects.postgresql import JSONB, UUID
    from sqlalchemy.ext.compiler import compiles

    @compiles(JSONB, "sqlite")
    def _compile_jsonb_sqlite(_type, _compiler, **_kw):  # type: ignore[no-untyped-def]
        return "JSON"

    @compiles(UUID, "sqlite")
    def _compile_uuid_sqlite(_type, _compiler, **_kw):  # type: ignore[no-untyped-def]
        return "CHAR(36)"


_patch_pg_types_for_sqlite()


# ── Step 2b — patch SQLAlchemy create_async_engine to ignore pool_size /
# max_overflow when the URL is sqlite-flavoured.
#
# ``app.db._make_engine`` passes ``pool_size`` + ``max_overflow``
# unconditionally; SQLite + the default aiosqlite pool reject them at
# ``create_engine`` call time. We install a monkey-patch BEFORE any
# ``app.*`` import so the module-import side effect (``engine =
# _make_engine()``) succeeds.


def _patch_create_async_engine_for_sqlite() -> None:
    from sqlalchemy.ext import asyncio as sa_async

    real = sa_async.create_async_engine

    def _create(url, **kw):  # type: ignore[no-untyped-def]
        url_str = str(url)
        if url_str.startswith("sqlite"):
            kw.pop("pool_size", None)
            kw.pop("max_overflow", None)
            # SQLite + a fresh AsyncEngine per call would give every
            # session a brand-new in-memory database. Pin a StaticPool
            # so the schema + rows survive across `get_session` calls.
            from sqlalchemy.pool import StaticPool

            kw.setdefault("poolclass", StaticPool)
            kw.setdefault("connect_args", {"check_same_thread": False})
        return real(url, **kw)

    sa_async.create_async_engine = _create  # type: ignore[assignment]


_patch_create_async_engine_for_sqlite()


# ── Step 2c — make SQLite return tz-aware datetimes.
#
# Production uses Postgres + ``TIMESTAMPTZ`` so every ``DateTime(timezone=
# True)`` column round-trips with its tzinfo. SQLite stores naive ISO
# strings and hands them back without tzinfo — so a
# ``expires_at < datetime.now(timezone.utc)`` comparison raises
# ``TypeError``. We coerce on load: any naive ``datetime`` value coming
# off the SQLite driver gets the UTC tz pasted on, matching production.


def _patch_sqlite_datetime_tz() -> None:
    """Force every ``DateTime(timezone=True)`` value loaded from SQLite
    to come back tz-aware.

    The SQLite + aiosqlite stack hands back naive ``datetime`` objects
    even when the column is declared ``timezone=True``. The production
    code (Postgres) compares those against ``datetime.now(timezone.utc)``
    and the comparison TypeErrors. We intercept by subclassing the
    SQLite dialect's underlying ``DATETIME`` impl and pasting UTC tz
    on every naive return.
    """
    from datetime import datetime, timezone as _tz

    from sqlalchemy.dialects.sqlite import base as sqlite_base

    real_cls = sqlite_base.DATETIME

    real_result_processor = real_cls.result_processor

    def process_result_value_with_tz(self, dialect, coltype):  # type: ignore[no-untyped-def]
        inner = real_result_processor(self, dialect, coltype)

        def proc(value):  # type: ignore[no-untyped-def]
            v = inner(value) if inner is not None else value
            if isinstance(v, datetime) and v.tzinfo is None:
                return v.replace(tzinfo=_tz.utc)
            return v

        return proc

    real_cls.result_processor = process_result_value_with_tz  # type: ignore[assignment]


_patch_sqlite_datetime_tz()


# ── Step 3 — engine + schema (session-scoped) ──────────────────────────


import pytest  # noqa: E402  (after env-seed + type-patch by design)
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@pytest_asyncio.fixture(scope="session")
async def _engine():
    """Single in-memory engine for the whole test session.

    Reuses the engine ``_patch_app_db_for_sqlite`` already installed on
    ``app.db`` so the test fixtures + the FastAPI app share one
    in-memory connection. Tables are created here; rows are cleared
    between tests by ``db_session``'s teardown.
    """
    from app import db as app_db
    from app.models import Base  # forces every model module to register

    engine = app_db.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def _sessionmaker(_engine):
    from app import db as app_db
    return app_db.SessionLocal


@pytest_asyncio.fixture
async def db_session(_sessionmaker, _engine) -> AsyncIterator[AsyncSession]:
    """One ``AsyncSession`` per test; tables truncated at teardown.

    SQLite + asyncpg-style nested transactions don't compose cleanly
    with FastAPI's ``Depends(get_session)`` (the app gets its own
    session from ``SessionLocal``), so we use a coarser strategy:
    drop every row in every table at teardown. The schema is reused.
    """
    from app.models import Base

    async with _sessionmaker() as session:
        try:
            yield session
        finally:
            await session.rollback()

    # Truncate every table — order matters for FKs, so reverse.
    async with _engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


# ── Step 4 — FastAPI app + ASGI client ────────────────────────────────


@pytest.fixture(scope="session")
def _app_factory():
    """Import + cache the FastAPI app exactly once."""
    # Local import — env + type patches must be in place first.
    from app.main import app as fastapi_app

    return fastapi_app


@pytest_asyncio.fixture
async def async_client(_app_factory, _engine):  # noqa: ARG001 — engine fixture forces schema init
    """``httpx.AsyncClient`` wired to the FastAPI app via ``ASGITransport``.

    ``app.db`` was already patched at module-import to point at the
    test SQLite engine, so ``Depends(get_session)`` yields the shared
    in-memory session automatically — no override needed. Cookies
    persist across calls on the same client instance, so login → /me
    round trips work.
    """
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=_app_factory)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ── Step 5 — auth + scope fixtures ────────────────────────────────────


@pytest_asyncio.fixture
async def auth_user(db_session, async_client):
    """Create a user, log them in, return ``(user, authed_client)``.

    The cookie is set on the ``async_client`` jar so subsequent calls
    are authenticated. The plaintext password is exposed as
    ``user._test_password`` for tests that need to exercise the
    change-password / re-login paths.
    """
    from app.auth import password as pw
    from app.auth.session import create_session, set_session_cookie
    from app.crypto import index as idx
    from app.crypto import kek as kek_mod
    from app.crypto import pii
    from app.models.user import ROLE_EDITOR, User

    email = f"test+{uuid.uuid4().hex[:8]}@example.com"
    password = "Correct-Horse-Battery-Staple-1!"

    user = User(
        email_index=idx.blind_index(email),
        email_encrypted=pii.encrypt_pii(email),
        name_encrypted=pii.encrypt_pii("Test User"),
        password_hash=pw.hash_password(password),
        kek_salt=pii.random_bytes(16),
        role=ROLE_EDITOR,
    )
    db_session.add(user)
    await db_session.commit()

    # Mint a session row + matching cookie so the existing
    # ``current_auth`` dependency resolves without a real login round
    # trip. Faster + doesn't depend on the /login schema being stable.
    kek = kek_mod.derive_kek(password, salt=user.kek_salt)
    session_row, session_secret = await create_session(db_session, user=user, kek=kek)
    await db_session.commit()

    # Set the cookie on the client jar.
    from app.auth.session import COOKIE_NAME
    import base64

    cookie_value = (
        f"{session_row.id}."
        f"{base64.urlsafe_b64encode(session_secret).decode('ascii').rstrip('=')}"
    )
    async_client.cookies.set(COOKIE_NAME, cookie_value)

    # Tests may want the plaintext password back for /login round trips.
    user._test_password = password  # type: ignore[attr-defined]
    return user, async_client


@pytest_asyncio.fixture
async def sample_run(db_session, auth_user):
    """Seed a project + run + one AuthorityMatch.

    Returns a dict so tests can name what they need:

        async def test_thing(sample_run):
            run_id = sample_run["run_id"]
            match_id = sample_run["match_id"]
    """
    from app.models.project import PROJECT_ROLE_OWNER, Membership, Project
    from app.models.run import (
        RUN_STATUS_SUCCEEDED, AuthorityMatch, Run, RunRecord,
    )

    user, client = auth_user

    project = Project(owner_id=user.id, name="Test project", description="")
    db_session.add(project)
    await db_session.flush()

    db_session.add(
        Membership(project_id=project.id, user_id=user.id, role=PROJECT_ROLE_OWNER),
    )

    run = Run(
        project_id=project.id,
        created_by=user.id,
        name="Test run",
        status=RUN_STATUS_SUCCEEDED,
        record_count=1,
        match_count=1,
    )
    db_session.add(run)
    await db_session.flush()

    record = RunRecord(
        run_id=run.id,
        control_number="990000000000000001",
        marc={
            "_control_number": "990000000000000001",
            "contributors":    [{"name": "Maimonides", "role": "author"}],
            "dates":           {"year": 1200},
        },
    )
    db_session.add(record)

    match = AuthorityMatch(
        run_id=run.id,
        control_number="990000000000000001",
        entity_text="Maimonides",
        entity_kind="person",
        role="author",
        matched_name="Moses Maimonides",
        viaf_id="100185956",
        wikidata_qid="Q127398",
        confidence="high",
        source="cross_source",
        payload={
            "sources":      ["viaf", "wikidata"],
            "source_count": 2,
            "birth_year":   1138,
            "death_year":   1204,
            "guard_flags":  [],
            "cluster_ids":  {"gnd": "118576488", "lccn": "n79006143"},
        },
        approved=False,
    )
    db_session.add(match)
    await db_session.commit()

    return {
        "user_id":         user.id,
        "project_id":      project.id,
        "run_id":          run.id,
        "control_number":  "990000000000000001",
        "match_id":        match.id,
        "client":          client,
    }


# ── Helper: stop the realtime listener task from being spawned ────────


@pytest.fixture(autouse=True, scope="session")
def _no_pg_listener() -> Iterator[None]:
    """Replace ``app.realtime.start_listener`` with a no-op for the
    duration of the test session.

    The production lifespan boots the asyncpg LISTEN bridge; against
    SQLite that bridge crashes. We patch it out at the source so the
    FastAPI app starts cleanly inside the test ASGI transport.
    """
    from app import realtime

    real_start = realtime.start_listener
    real_stop = realtime.stop_listener

    async def _noop_start() -> None:
        return None

    async def _noop_stop() -> None:
        return None

    realtime.start_listener = _noop_start  # type: ignore[assignment]
    realtime.stop_listener = _noop_stop  # type: ignore[assignment]
    try:
        yield
    finally:
        realtime.start_listener = real_start  # type: ignore[assignment]
        realtime.stop_listener = real_stop  # type: ignore[assignment]
