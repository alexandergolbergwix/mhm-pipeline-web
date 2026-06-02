"""Async SQLAlchemy engine + session helpers."""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.settings import get_settings


def _ssl_connect_args(url: str) -> dict[str, Any]:
    """Build asyncpg connect_args that force SSL on managed Postgres.

    Heroku Postgres (and Aurora-backed Heroku Essential plans) refuse
    unencrypted connections — they reply with::

        no pg_hba.conf entry for host "X.Y.Z.W", user "...", database "...",
        no encryption

    The asyncpg driver does NOT enable SSL by default, so we have to
    pass ``ssl="require"`` (or an SSLContext) via connect_args. We
    skip the flag for sqlite and explicit localhost URLs so local dev
    + the in-memory test fixture keep working.
    """
    if url.startswith("sqlite"):
        return {}
    if "@localhost" in url or "@127.0.0.1" in url or "@host.docker.internal" in url:
        return {}
    # Heroku Postgres certs are valid but the hostname matches an
    # AWS-internal record; check_hostname=False keeps it working
    # without requiring an extra CA bundle on the dyno.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return {"ssl": ctx}


def _make_engine():
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        connect_args=_ssl_connect_args(settings.database_url),
    )


engine = _make_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an :class:`AsyncSession`."""
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Plain async context manager for scripts and tests."""
    async with SessionLocal() as session:
        yield session
