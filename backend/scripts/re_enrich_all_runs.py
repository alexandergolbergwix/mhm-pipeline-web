"""Re-enrich authority matches for every run (or one project).

Usage (recommended — runs on Heroku where DATABASE_URL is already set):
  heroku run -- bash -lc "cd backend && AUTHORITY_MODE=postgres \\
    python -m scripts.re_enrich_all_runs --skip-cache" -a mhm-pipeline-web

Usage (local against Heroku Postgres — export URL first, never inline subshell):
  export DATABASE_URL="$(heroku config:get DATABASE_URL -a mhm-pipeline-web | tr -d '\\n')"
  export AUTHORITY_MODE=postgres
  cd backend && .venv/bin/python -m scripts.re_enrich_all_runs --skip-cache
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid

from sqlalchemy import select

from app.db import session_scope
from app.models.run import AuthorityMatch, Run, RunRecord
from app.pipeline import authority as auth_pipeline
from app.pipeline.authority_re_enrich import re_enrich_run
from app.settings import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _validate_runtime() -> None:
    """Fail fast when re-enrich would hit the wrong database or authority backend."""
    url = get_settings().database_url
    if "localhost" in url or "127.0.0.1" in url:
        logger.error(
            "database_url points at localhost (%s). "
            "Export Heroku DATABASE_URL or use `heroku run` — see script docstring.",
            url[:48],
        )
        sys.exit(1)
    mode = __import__("os").environ.get("AUTHORITY_MODE", "local").lower()
    if mode != "postgres":
        logger.error(
            "AUTHORITY_MODE=%r — set AUTHORITY_MODE=postgres for production re-enrich.",
            mode or "local",
        )
        sys.exit(1)


async def _run(project_id: uuid.UUID | None, skip_cache: bool) -> None:
    matcher = auth_pipeline.get_default_matcher()
    async with session_scope() as db:
        q = select(Run).order_by(Run.created_at.desc())
        if project_id is not None:
            q = q.where(Run.project_id == project_id)
        runs = (await db.execute(q)).scalars().all()
        logger.info("Re-enriching %d run(s)", len(runs))
        for run in runs:
            records = (
                await db.execute(select(RunRecord).where(RunRecord.run_id == run.id))
            ).scalars().all()
            existing = (
                await db.execute(
                    select(AuthorityMatch).where(AuthorityMatch.run_id == run.id),
                )
            ).scalars().all()
            stats = await re_enrich_run(
                db, run, matcher,
                skip_cache=skip_cache,
                records=list(records),
                existing_rows=list(existing),
            )
            logger.info("run %s: %s", run.id, stats)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk authority re-enrich")
    parser.add_argument("--project-id", type=uuid.UUID, default=None)
    parser.add_argument("--skip-cache", action="store_true")
    parser.add_argument(
        "--allow-local",
        action="store_true",
        help="Permit localhost database_url (dev only)",
    )
    args = parser.parse_args()
    if not args.allow_local:
        _validate_runtime()
    asyncio.run(_run(args.project_id, args.skip_cache))


if __name__ == "__main__":
    main()
