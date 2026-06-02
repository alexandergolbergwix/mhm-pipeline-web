"""Heroku Scheduler entrypoint for the daily event-log prune.

Invoke (from inside ``backend/``)::

    python -m scripts.run_prune_events

Configured under Heroku Scheduler to run once a day at 03:05 UTC (five
minutes after ``run_purge`` so the two retention jobs do not contend
on the same connection pool). See ``docs/DEPLOY.md`` § 6.6 for the
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
from app.jobs.prune_events import prune_events  # noqa: E402

logger = logging.getLogger(__name__)


async def _main() -> None:
    async with session_scope() as db:
        summary = await prune_events(db)
    print(
        f"run_prune_events: entities_pruned={summary['entities_pruned']} "
        f"events_deleted={summary['events_deleted']}"
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())


if __name__ == "__main__":
    main()
