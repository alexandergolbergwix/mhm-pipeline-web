"""Worker-dyno entrypoint (job-service R27).

Runs the run_jobs execution loop off the web dyno: startup recovery plus
the maintenance loop (heartbeat, stale reaping, orphan respawn, queued
admission). Jobs are DB rows — the claim protocol in
``run_job_service`` arbitrates between web and worker processes, so the
web app may still spawn its own jobs (light kinds always; heavy kinds
after the worker grace window if no worker is alive).

Start with: ``python -m app.jobs_worker`` (see scripts/start_worker.sh).
"""

from __future__ import annotations

import asyncio
import logging

from app.pipeline.run_job_service import (
    admit_waiting_jobs,
    recover_interrupted_jobs,
    recover_resumable_verify_jobs,
    run_job_maintenance_tick,
    startup_job_recovery,
)

logger = logging.getLogger(__name__)


async def main() -> None:
    await startup_job_recovery()
    await recover_resumable_verify_jobs()
    await admit_waiting_jobs()

    from app.pipeline.run_job_service import (  # noqa: PLC0415
        _int_env,
        MAINTENANCE_INTERVAL_SECONDS,
    )
    import os  # noqa: PLC0415

    interval = _int_env("RUN_JOB_MAINTENANCE_INTERVAL", MAINTENANCE_INTERVAL_SECONDS)
    logger.info("jobs worker started (tick every %ds)", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_job_maintenance_tick()
        except Exception:  # noqa: BLE001 — the loop must survive any tick failure
            logger.exception("jobs worker tick failed")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
