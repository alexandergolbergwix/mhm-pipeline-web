"""Heroku Scheduler entry-point: daily inference_cache prune.

Invoke (from inside ``backend/``)::

    python -m scripts.run_prune_inference_cache

Or locally::

    cd backend && .venv/bin/python -m scripts.run_prune_inference_cache

Configured under Heroku Scheduler to run once a day at 02:05 UTC (before the
03:05 UTC event-log prune so the two jobs do not contend on the same
connection pool at the same time). See ``docs/DEPLOY.md`` § 6.6 for the
add-on setup.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path


def _ensure_app_on_syspath() -> None:
    """Make ``app.*`` importable when invoked as a top-level script."""
    backend_dir = Path(__file__).resolve().parent.parent
    backend_str = str(backend_dir)
    if backend_str not in sys.path:
        sys.path.insert(0, backend_str)


_ensure_app_on_syspath()

from app.db import session_scope  # noqa: E402
from app.jobs.prune_inference_cache import prune_inference_cache  # noqa: E402

logger = logging.getLogger(__name__)


async def _main() -> None:
    async with session_scope() as db:
        summary = await prune_inference_cache(db)
    print(f"run_prune_inference_cache: deleted={summary['deleted']}")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())


if __name__ == "__main__":
    main()
