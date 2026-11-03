"""Connection-budget changes (Rule W-285).

The production Postgres is `essential-1` with a hard **20-connection** cap
(`heroku pg:info` → `Connections: N/20`), and `app/db.py` used to give every
process `pool_size=5 + max_overflow=10` = 15. A rule-verify fan-out runs a
web dyno, a worker and several Modal shards at once, so a single run wanted
~90 connections against a ceiling of 20 — a build died mid-refresh and
progress callbacks went missing under `too many connections for role`.

Three changes: small tunable pools, an optional PgBouncer route for the
shared engine, and the LISTEN bridge pinned to a direct session.
"""
from __future__ import annotations

from app.settings import Settings


def _postgres_engine(monkeypatch, **env: str):
    """An engine over a Postgres URL, so the QueuePool sizes are real."""
    from app import db as dbmod

    monkeypatch.setattr(
        dbmod, "get_settings",
        lambda: Settings(
            pg_bouncer_url="", 
            database_url="postgresql+asyncpg://u:p@db.internal:5432/db",
        ),
    )
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return dbmod._make_engine()


def test_pool_is_small_by_default(monkeypatch) -> None:
    """15-per-process was the whole arithmetic problem."""
    engine = _postgres_engine(monkeypatch)
    try:
        assert engine.pool._pool.maxsize == 1
        assert engine.pool._max_overflow == 2
    finally:
        engine.sync_engine.dispose()


def test_pool_is_tunable_without_a_deploy(monkeypatch) -> None:
    engine = _postgres_engine(monkeypatch, DB_POOL_SIZE="3", DB_MAX_OVERFLOW="5")
    try:
        assert engine.pool._pool.maxsize == 3
        assert engine.pool._max_overflow == 5
    finally:
        engine.sync_engine.dispose()


def test_pool_int_rejects_junk_and_clamps_low() -> None:
    from app.db import _pool_int

    assert _pool_int("DB_POOL_SIZE", 1) == 1  # unset -> default
    os = __import__("os")
    os.environ["_T_POOL"] = "0"
    try:
        assert _pool_int("_T_POOL", 7) == 1  # never below 1
    finally:
        os.environ.pop("_T_POOL", None)
    os.environ["_T_POOL"] = "not-a-number"
    try:
        assert _pool_int("_T_POOL", 7) == 7  # junk -> default
    finally:
        os.environ.pop("_T_POOL", None)


def test_engine_uses_the_pg_bouncer_route_when_configured() -> None:
    """The shared engine multiplexes through PgBouncer when one is set."""
    from app import db as dbmod

    settings = Settings(
        pg_bouncer_url="postgresql+asyncpg://u:p@pb.internal:6432/db",
        database_url="postgresql+asyncpg://u:p@db.internal:5432/db",
    )
    engine = dbmod.create_async_engine(
        settings.pg_bouncer_url or settings.database_url,
        pool_size=1,
    )
    try:
        assert "pb.internal" in str(engine.url)
        assert "db.internal" not in str(engine.url)
    finally:
        engine.sync_engine.dispose()


def test_engine_falls_back_to_the_direct_url() -> None:
    from app import db as dbmod

    settings = Settings(
        pg_bouncer_url="",
        database_url="postgresql+asyncpg://u:p@db.internal:5432/db",
    )
    engine = dbmod.create_async_engine(
        settings.pg_bouncer_url or settings.database_url,
        pool_size=1,
    )
    try:
        assert "db.internal" in str(engine.url)
    finally:
        engine.sync_engine.dispose()


def test_listeners_bind_to_the_direct_url_never_the_pooler() -> None:
    """LISTEN is session-level; it dies under transaction pooling."""
    import asyncio

    from app.realtime import _asyncpg_dsn

    dsn = asyncio.run(_asyncpg_dsn())
    assert dsn.startswith("postgresql://")
    # Whatever pg_bouncer_url is set to, the listener must not use it.
    assert "6432" not in dsn.split("@")[-1].split("/")[0]
