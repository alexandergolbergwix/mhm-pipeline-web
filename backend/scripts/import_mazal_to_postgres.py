"""Import the Mazal NLI authority SQLite database into Heroku Postgres.

Run once (idempotent — TRUNCATE + re-import):

    cd backend
    DATABASE_URL=... MAZAL_DB_PATH=... .venv/bin/python -m scripts.import_mazal_to_postgres

Or on Heroku (after migration 0018 has run):

    heroku run -- bash -lc "cd backend && MAZAL_DB_PATH=/app/converter/authority/mazal_index.db python -m scripts.import_mazal_to_postgres"

Speed notes
-----------
Uses psycopg2 COPY with an in-memory CSV buffer per chunk (10 000 rows).
On a Heroku Essential-1 Postgres, 2.5 M authorities + 5.3 M name-index
rows finish in 5–10 min depending on dyno size and network.

The script is idempotent: it TRUNCATEs both tables before import so a
re-run from a refreshed SQLite is safe.
"""

from __future__ import annotations

import io
import logging
import os
import sqlite3
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

CHUNK = 10_000


def _pg_dsn() -> str:
    dsn = os.getenv("DATABASE_URL", "")
    if dsn.startswith("postgres://"):
        dsn = dsn.replace("postgres://", "postgresql://", 1)
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")
    return dsn


def _mazal_path() -> str:
    p = os.getenv("MAZAL_DB_PATH", "")
    if not p:
        # Try next to this script or the converter directory
        candidates = [
            os.path.join(os.path.dirname(__file__), "..", "converter", "authority", "mazal_index.db"),
            "/app/converter/authority/mazal_index.db",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
    if p and not os.path.exists(p):
        raise RuntimeError(f"Mazal DB not found at {p!r}")
    if not p:
        raise RuntimeError("MAZAL_DB_PATH is not set and auto-detection failed")
    return p


def _copy_rows(pg_cur: object, table: str, columns: list[str], rows: list[tuple]) -> None:
    buf = io.StringIO()
    for row in rows:
        parts = []
        for v in row:
            if v is None:
                parts.append("\\N")
            else:
                # Escape backslashes, tabs, newlines for COPY TEXT format
                s = str(v).replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")
                parts.append(s)
        buf.write("\t".join(parts) + "\n")
    buf.seek(0)
    pg_cur.copy_from(buf, table, columns=columns, null="\\N")


def import_authorities(pg_cur: object, sq_conn: sqlite3.Connection) -> int:
    logger.info("Truncating mazal_authorities …")
    pg_cur.execute("TRUNCATE mazal_authorities CASCADE")

    total = sq_conn.execute("SELECT COUNT(*) FROM authorities").fetchone()[0]
    logger.info("Importing %d authority rows …", total)

    imported = 0
    chunk: list[tuple] = []
    cols = ["nli_id", "entity_type", "preferred_name_heb", "preferred_name_lat", "dates", "aleph_id"]
    for row in sq_conn.execute("SELECT nli_id, entity_type, preferred_name_heb, preferred_name_lat, dates, aleph_id FROM authorities"):
        chunk.append(row)
        if len(chunk) >= CHUNK:
            _copy_rows(pg_cur, "mazal_authorities", cols, chunk)
            imported += len(chunk)
            chunk = []
            if imported % 100_000 == 0:
                logger.info("  authorities: %d / %d", imported, total)
    if chunk:
        _copy_rows(pg_cur, "mazal_authorities", cols, chunk)
        imported += len(chunk)
    logger.info("  authorities done: %d rows", imported)
    return imported


def import_name_index(pg_cur: object, sq_conn: sqlite3.Connection) -> int:
    logger.info("Truncating mazal_name_index …")
    pg_cur.execute("TRUNCATE mazal_name_index CASCADE")

    total = sq_conn.execute("SELECT COUNT(*) FROM name_index").fetchone()[0]
    logger.info("Importing %d name_index rows …", total)

    imported = 0
    chunk: list[tuple] = []
    cols = ["normalized_name", "nli_id", "entity_type", "script"]
    for row in sq_conn.execute("SELECT normalized_name, nli_id, entity_type, script FROM name_index"):
        chunk.append(row)
        if len(chunk) >= CHUNK:
            _copy_rows(pg_cur, "mazal_name_index", cols, chunk)
            imported += len(chunk)
            chunk = []
            if imported % 500_000 == 0:
                logger.info("  name_index: %d / %d", imported, total)
    if chunk:
        _copy_rows(pg_cur, "mazal_name_index", cols, chunk)
        imported += len(chunk)
    logger.info("  name_index done: %d rows", imported)
    return imported


def main() -> None:
    import psycopg2  # noqa: PLC0415

    mazal_path = _mazal_path()
    logger.info("Mazal SQLite: %s", mazal_path)

    sq = sqlite3.connect(mazal_path)
    sq.row_factory = None

    dsn = _pg_dsn()
    pg = psycopg2.connect(dsn)
    pg.autocommit = False
    cur = pg.cursor()

    t0 = time.time()
    try:
        n_auth = import_authorities(cur, sq)
        n_idx = import_name_index(cur, sq)
        pg.commit()
        elapsed = time.time() - t0
        logger.info(
            "Mazal import complete: %d authorities + %d name-index rows in %.1f s",
            n_auth, n_idx, elapsed,
        )
    except Exception:
        pg.rollback()
        logger.exception("Import failed — rolled back")
        sys.exit(1)
    finally:
        cur.close()
        pg.close()
        sq.close()


if __name__ == "__main__":
    main()
