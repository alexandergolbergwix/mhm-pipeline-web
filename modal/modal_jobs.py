"""mhm-jobs — Modal execution for heavy pipeline jobs (Rule W-237).

Deploy target only (Rule W-15: the backend never imports this file).
When ``MODAL_JOBS_URL`` is configured on Heroku, ``rdf_build`` and
``hmo_item_build`` jobs execute here instead of the 512 MB web dyno:

- The Heroku web process claims the job (admission + heavy cap + cancel
  arbitration stay in ``run_job_service``) and dispatches
  ``POST /run {job_id, kind}`` with a shared bearer token.
- This app spawns a detached container that runs the SAME backend job
  runner against Heroku Postgres (``DATABASE_URL`` lives in the
  ``mhm-jobs`` Modal secret) and writes progress + terminal state
  directly to ``run_jobs``.
- The Heroku process polls the row and falls back to local execution if
  Modal never reports a terminal state (the RDF build resumes from its
  checkpoint).

Auth model: the bearer token proves Heroku-side origin; ``DATABASE_URL``
is the only secret here. Wiki credentials and publication tokens never
enter this container (Rules W-217 / W-228 boundaries).

Deploy:
    cd modal && modal deploy modal_jobs.py

Set on Heroku:
    heroku config:set MODAL_JOBS_URL=https://<workspace>--mhm-jobs-run.modal.run \\
      MODAL_JOBS_TOKEN=<same value as the Modal secret>
"""
import os

import modal

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_TIMEOUT_S = 43200  # 12 h — verify of an 18k scope at 4-way parallel needs it

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_pyproject(
        os.path.join(_ROOT, "backend", "pyproject.toml"),
    )
    # eval-agent runtime (hmo_item_verify executor, W-247): tiny deps the
    # backend image may not carry.
    .pip_install("pyyaml>=6.0", "jsonschema>=4.20")
    .add_local_dir(
        os.path.join(_ROOT, "backend"),
        remote_path="/root/backend",
        copy=True,
    )
    .add_local_dir(
        os.path.join(_ROOT, "backend", "ontology"),
        remote_path="/root/ontology",
        copy=True,
    )
    .add_local_dir(
        os.path.join(_ROOT, "eval-agent"),
        remote_path="/root/eval-agent",
        copy=True,
    )
    .env({
        # /root/eval-agent must be importable: agent_runner spawns the
        # eval-agent subprocess which imports the `eval_agent` package
        # (2026-09-18: missing path → ModuleNotFoundError → the verify
        # container died without finalising the job row → zombie).
        "PYTHONPATH": "/root/backend:/root/eval-agent",
        "EVAL_AGENT_ROOT": "/root/eval-agent",
    })
)

app = modal.App(
    "mhm-jobs",
    secrets=[modal.Secret.from_name("mhm-jobs2")],
)


def _authorize(authorization: str | None) -> bool:
    import hmac

    expected = os.environ.get("MODAL_JOBS_TOKEN", "")
    if not expected:
        return False
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    return hmac.compare_digest(supplied, expected)


async def _heartbeat_claim(job_id: str, executor_id: str) -> None:
    """Refresh the executor lease every minute (W-244).

    The container must not depend on the dispatching process's poller for
    liveness: the stale reap runs on the web dyno and marks a running row
    failed when ``updated_at`` goes quiet. A long quiet CPU stretch (the
    HMO item export's threadpool call takes minutes) then killed the job
    mid-compute (2026-09-18). Guarded by ``claimed_by`` so a stolen lease
    is never zombie-heartbeated.
    """
    from sqlalchemy import update

    import asyncio  # noqa: PLC0415

    from app.db import session_scope
    from app.models.run_job import RunJob

    from datetime import datetime, timezone  # noqa: PLC0415

    while True:
        await asyncio.sleep(60)
        try:
            async with session_scope() as db:
                await db.execute(
                    update(RunJob)
                    .where(
                        RunJob.id == job_id,
                        RunJob.status == "running",
                        RunJob.claimed_by == executor_id,
                    )
                    .values(updated_at=datetime.now(timezone.utc))
                    .execution_options(synchronize_session=False),
                )
                await db.commit()
        except Exception:  # noqa: BLE001 — heartbeat is best-effort
            pass


def _run_job_detached(job_id: str, kind: str, callback_url: str = "") -> dict:
    """Container entry: run the exact Heroku job runner for one claimed row."""
    import asyncio
    import uuid as _uuid

    async def _execute() -> dict:
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import select, update

        from app.db import session_scope
        from app.models.run_job import RunJob

        executor_id = f"modal-executor:{_uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        stale = now - timedelta(seconds=120)

        # Exclusive lease: exactly one container may run this job. A
        # preemption restart re-enters here while the dead container's
        # claim may still look fresh — sleep ONCE until its heartbeat
        # would have gone stale (no busy waiting), then take over.
        acquired = False
        for _attempt in range(3):
            async with session_scope() as db:
                row = (
                    await db.execute(select(RunJob).where(RunJob.id == job_id))
                ).scalar_one_or_none()
                if row is None or row.status != "running":
                    return {"ok": False, "job_id": job_id, "error": "row not running"}
                res = await db.execute(
                    update(RunJob)
                    .where(
                        RunJob.id == job_id,
                        RunJob.status == "running",
                        (
                            (RunJob.claimed_by == "modal-dispatch")
                            | (
                                RunJob.claimed_by.like("modal-executor:%")
                                & (RunJob.updated_at < stale)
                            )
                        ),
                    )
                    .values(claimed_by=executor_id, updated_at=datetime.now(timezone.utc))
                    .execution_options(synchronize_session=False),
                )
                await db.commit()
            if (res.rowcount or 0) == 1:
                acquired = True
                break
            # Lease held by a container that may still be alive: sleep until
            # its claim would be stale, then retry (bounded).
            await asyncio.sleep(125)
            stale = datetime.now(timezone.utc) - timedelta(seconds=120)
        if not acquired:
            return {"ok": False, "job_id": job_id, "error": "lease not acquired"}

        heartbeat_task = asyncio.create_task(_heartbeat_claim(job_id, executor_id))
        try:
            if kind == "rdf_build":
                from app.pipeline.rdf_build_job import run_rdf_build_job

                await run_rdf_build_job(job_id)
            elif kind == "hmo_item_build":
                from app.pipeline.hmo_item_build_job import run_hmo_item_build_job

                await run_hmo_item_build_job(job_id)
            elif kind == "hmo_item_verify":
                # W-247: the eval-agent subprocess + 18k-item scope need the
                # container's 8 GB — the 512 MB web dyno thrashed (R14).
                from app.pipeline.verify_job import run_verify_job

                await run_verify_job(job_id)
            else:
                raise ValueError(f"kind {kind!r} has no Modal executor")
        except Exception as exc:  # noqa: BLE001
            # A dead runner must never leave a zombie: fail the row HERE so
            # the web-side waiter sees a terminal state (2026-09-18: the
            # verify container died on an eval-agent ImportError and the
            # row ran "forever" on web-side heartbeats, Rule W-247).
            try:
                from app.db import session_scope as _ss
                from app.models.run_job import (
                    JOB_STATUS_FAILED,
                    RunJob as _RunJob,
                )
                from sqlalchemy import update as _update

                async with _ss() as _db:
                    await _db.execute(
                        _update(_RunJob)
                        .where(
                            _RunJob.id == job_id,
                            _RunJob.status == "running",
                        )
                        .values(
                            status=JOB_STATUS_FAILED,
                            error=f"Modal runner crashed: {type(exc).__name__}: {exc}"[:400],
                        )
                    )
                    await _db.commit()
            except Exception:  # noqa: BLE001 — best-effort finalisation
                pass
            raise
        finally:
            heartbeat_task.cancel()

        # Completion webhook — wakes the Heroku poller instantly (it also
        # runs a slow row check as the safety net, so a missed webhook is
        # not fatal).
        if callback_url:
            import httpx

            token = os.environ.get("MODAL_JOBS_TOKEN", "")
            for attempt in range(3):
                try:
                    resp = await httpx.AsyncClient(timeout=15.0).post(
                        callback_url,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    if resp.status_code < 500:
                        break
                except Exception:  # noqa: BLE001 — webhook is best-effort
                    pass
                await asyncio.sleep(2.0 * (attempt + 1))
        return {"ok": True, "job_id": job_id, "kind": kind}

    return asyncio.run(_execute())


@app.function(
    image=image,
    cpu=2,
    memory=8192,
    timeout=_TIMEOUT_S,
    secrets=[modal.Secret.from_name("mhm-jobs2")],
)
def run_modal_job_detached(job_id: str, kind: str, callback_url: str = "") -> dict:
    return _run_job_detached(job_id, kind, callback_url)


@app.function(
    image=image,
    cpu=1,
    memory=512,
    scaledown_window=300,
    secrets=[modal.Secret.from_name("mhm-jobs2")],
)
@modal.fastapi_endpoint(label="mhm-jobs-run", method="POST")
def run(request_body: dict) -> dict:
    """Dispatch endpoint: spawn the detached runner, return 202.

    The Heroku process polls the ``run_jobs`` row; this endpoint only
    validates auth + row state and spawns.
    """
    from fastapi import HTTPException

    from starlette.requests import Request as StarletteRequest

    assert isinstance(request_body, dict)

    # fastapi_endpoint injects no Request by default — auth comes from the body
    # token field to stay dependency-light.
    if not _authorize(str(request_body.get("token") or "")):
        raise HTTPException(status_code=401, detail="invalid token")

    job_id = str(request_body.get("job_id") or "")
    kind = str(request_body.get("kind") or "")
    if not job_id or kind not in ("rdf_build", "hmo_item_build", "hmo_item_verify"):
        raise HTTPException(status_code=422, detail="job_id and kind required")

    # Row must be running AND hold the dispatch lease (the Heroku client
    # sets claimed_by='modal-dispatch' right before calling us — this is
    # what makes double dispatch after a web restart harmless).
    import asyncio

    async def _check() -> tuple[str | None, str | None]:
        from sqlalchemy import select

        from app.db import session_scope
        from app.models.run_job import RunJob

        async with session_scope() as db:
            job = (
                await db.execute(select(RunJob).where(RunJob.id == job_id))
            ).scalar_one_or_none()
        if job is None:
            return None, None
        return str(job.status), str(job.claimed_by or "")

    status, claimed_by = asyncio.run(_check())
    if status != "running":
        raise HTTPException(
            status_code=409,
            detail=f"job {job_id} is {status or 'missing'}, expected running",
        )
    if claimed_by != "modal-dispatch":
        raise HTTPException(
            status_code=409,
            detail=(
                f"job {job_id} is not holding the dispatch lease "
                f"(claimed_by={claimed_by or 'none'})"
            ),
        )

    run_modal_job_detached.spawn(job_id, kind, str(request_body.get("callback_url") or ""))
    return {"ok": True, "spawned": True}
