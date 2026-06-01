"""Heroku Scheduler entrypoint for the daily GDPR retention purge.

Invoke (from inside ``backend/``)::

    python -m scripts.run_purge

Configured under Heroku Scheduler to run once a day at 03:00 UTC. See
``docs/DEPLOY.md`` § 6.6 for the add-on setup.
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
from app.jobs.purge_access_requests import purge_access_requests  # noqa: E402

logger = logging.getLogger(__name__)


async def _main() -> None:
    async with session_scope() as db:
        summary = await purge_access_requests(db)
    print(
        f"run_purge: abandoned={summary['abandoned_purged']} "
        f"denied={summary['denied_purged']} "
        f"stale_pending={summary['stale_pending']}"
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())


if __name__ == "__main__":
    main()
