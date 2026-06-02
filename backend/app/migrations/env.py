"""Alembic env — async-aware, sources the URL from app settings."""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.models import Base  # noqa: F401 — register every model on Base.metadata
from app.settings import get_settings

config = context.config
target_metadata = Base.metadata


def _set_url() -> None:
    settings = get_settings()
    config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    _set_url()
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    _set_url()
    # Reuse the same SSL-aware connect_args the app uses — Heroku
    # Postgres refuses unencrypted asyncpg connections, so alembic
    # release-phase would fail with "no pg_hba.conf entry … no
    # encryption" without this.
    from app.db import _ssl_connect_args  # noqa: PLC0415
    settings = get_settings()
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=_ssl_connect_args(settings.database_url),
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
