"""Heroku Scheduler entrypoint for the 3x/day entity snapshot job.

Invoke (from inside ``backend/``)::

    python -m scripts.run_snapshot

Configured under Heroku Scheduler to run three times a day at 00:05,
08:05, and 16:05 UTC. See ``docs/DEPLOY.md`` § 6.7 for the add-on setup.
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
from app.jobs.snapshot_entities import snapshot_touched_entities  # noqa: E402

logger = logging.getLogger(__name__)


async def _main() -> None:
    async with session_scope() as db:
        summary = await snapshot_touched_entities(db)
    print(
        f"run_snapshot: snapshots_written={summary['snapshots_written']} "
        f"entities_touched={summary['entities_touched']}"
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())


if __name__ == "__main__":
    main()
