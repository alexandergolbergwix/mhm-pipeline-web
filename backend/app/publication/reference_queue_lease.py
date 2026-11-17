"""Keep one reference writer per run across hosts and dynos."""

import hashlib
from contextlib import asynccontextmanager

from sqlalchemy import text


@asynccontextmanager
async def reference_queue_lease(engine, run_id):
    if engine.dialect.name != "postgresql":
        raise ValueError("The reference writer requires a Postgres lease.")
    key = int.from_bytes(
        hashlib.sha256(f"reference-queue:{run_id}".encode()).digest()[:8], "big", signed=True
    )
    async with engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        if not acquired:
            raise ValueError("Another host already owns this reference queue.")
        try:
            yield
        finally:
            await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


@asynccontextmanager
async def direct_reference_queue_lease(run_id):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.db import _ssl_connect_args
    from app.settings import get_settings

    url = get_settings().database_url
    engine = create_async_engine(url, poolclass=NullPool, connect_args=_ssl_connect_args(url))
    try:
        async with reference_queue_lease(engine, run_id):
            yield
    finally:
        await engine.dispose()
