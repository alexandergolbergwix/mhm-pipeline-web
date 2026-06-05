"""Import the KIMA Hebrew place-names SQLite database into Heroku Postgres.

Run once (idempotent — TRUNCATE + re-import):

    cd backend
    DATABASE_URL=... .venv/bin/python -m scripts.import_kima_to_postgres

Or on Heroku (after migration 0018 has run):

    heroku run -- bash -lc "cd backend && python -m scripts.import_kima_to_postgres"

KIMA is only 15 MB / ~177 K rows; the import completes in under a minute.
The script is idempotent: it TRUNCATEs both tables before import.
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

CHUNK = 5_000


def _pg_dsn() -> str:
    dsn = os.getenv("DATABASE_URL", "")
    if dsn.startswith("postgres://"):
        dsn = dsn.replace("postgres://", "postgresql://", 1)
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")
    return dsn


def _kima_path() -> str:
    p = os.getenv("KIMA_DB_PATH", "")
    if not p:
        candidates = [
            os.path.join(os.path.dirname(__file__), "..", "data", "kima", "kima_index.db"),
            "/app/data/kima/kima_index.db",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
    if p and not os.path.exists(p):
        raise RuntimeError(f"KIMA DB not found at {p!r}")
    if not p:
        raise RuntimeError("KIMA_DB_PATH is not set and auto-detection failed")
    return p


def _copy_rows(pg_cur: object, table: str, columns: list[str], rows: list[tuple]) -> None:
    buf = io.StringIO()
    for row in rows:
        parts = []
        for v in row:
            if v is None:
                parts.append("\\N")
            else:
                s = str(v).replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")
                parts.append(s)
        buf.write("\t".join(parts) + "\n")
    buf.seek(0)
    pg_cur.copy_from(buf, table, columns=columns, null="\\N")


def import_places(pg_cur: object, sq_conn: sqlite3.Connection) -> int:
    logger.info("Truncating kima_places …")
    pg_cur.execute("TRUNCATE kima_places CASCADE")

    total = sq_conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
    logger.info("Importing %d place rows …", total)

    imported = 0
    chunk: list[tuple] = []
    cols = ["kima_id", "primary_heb", "primary_rom", "wikidata_id", "viaf_id", "geonames_id", "mazal_nli_id", "lat", "lon"]
    for row in sq_conn.execute(
        "SELECT kima_id, primary_heb, primary_rom, wikidata_id, viaf_id, geonames_id, mazal_nli_id, lat, lon FROM places"
    ):
        chunk.append(row)
        if len(chunk) >= CHUNK:
            _copy_rows(pg_cur, "kima_places", cols, chunk)
            imported += len(chunk)
            chunk = []
    if chunk:
        _copy_rows(pg_cur, "kima_places", cols, chunk)
        imported += len(chunk)
    logger.info("  places done: %d rows", imported)
    return imported


def import_name_index(pg_cur: object, sq_conn: sqlite3.Connection) -> int:
    logger.info("Truncating kima_name_index …")
    pg_cur.execute("TRUNCATE kima_name_index CASCADE")

    total = sq_conn.execute("SELECT COUNT(*) FROM name_index").fetchone()[0]
    logger.info("Importing %d name_index rows …", total)

    imported = 0
    chunk: list[tuple] = []
    cols = ["normalized_name", "kima_id", "script"]
    skipped = 0
    for row in sq_conn.execute("SELECT normalized_name, kima_id, script FROM name_index"):
        # Skip corrupt entries (concatenated blobs — one entry is ~900 KB)
        if row[0] and len(row[0]) > 500:
            skipped += 1
            continue
        chunk.append(row)
        if len(chunk) >= CHUNK:
            _copy_rows(pg_cur, "kima_name_index", cols, chunk)
            imported += len(chunk)
            chunk = []
    if chunk:
        _copy_rows(pg_cur, "kima_name_index", cols, chunk)
        imported += len(chunk)
    if skipped:
        logger.warning("  name_index: skipped %d rows with normalized_name > 500 chars", skipped)
    logger.info("  name_index done: %d rows", imported)
    return imported


def main() -> None:
    import psycopg2  # noqa: PLC0415

    kima_path = _kima_path()
    logger.info("KIMA SQLite: %s", kima_path)

    sq = sqlite3.connect(kima_path)
    sq.row_factory = None

    dsn = _pg_dsn()
    pg = psycopg2.connect(dsn)
    pg.autocommit = False
    cur = pg.cursor()

    t0 = time.time()
    try:
        n_places = import_places(cur, sq)
        n_idx = import_name_index(cur, sq)
        pg.commit()
        elapsed = time.time() - t0
        logger.info(
            "KIMA import complete: %d places + %d name-index rows in %.1f s",
            n_places, n_idx, elapsed,
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
