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
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import modal

logger = logging.getLogger(__name__)

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_TIMEOUT_S = 43200  # 12 h — verify of an 18k scope at 4-way parallel needs it


def _materialize_committed_tree() -> str:
    """Extract HEAD into a temp dir — deploy the COMMITTED backend, not the
    dirty working tree (2026-09-19: a parallel session's half-finished
    edits made containers die at import while the row kept running).
    Modal builds images at deploy time, so this runs locally where git
    exists. Copying the whole tree once keeps relative paths intact.
    """
    import tarfile

    tmp = tempfile.mkdtemp(prefix="mhm-modal-head-")
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tf:
        tar_path = tf.name
    subprocess.run(
        ["git", "archive", "HEAD", "--output", tar_path],
        cwd=_ROOT, check=True,
    )
    with tarfile.open(tar_path) as archive:
        archive.extractall(tmp)
    os.unlink(tar_path)
    return tmp


_HEAD = _materialize_committed_tree() if modal.is_local() else "/root"
# modal.is_local(): the git-archive extraction must happen ONLY at deploy
# time (client side has git). Inside a container the module is re-imported
# on every cold start (2026-09-19: `git` is absent from debian_slim →
# FileNotFoundError at import → the app crash-looped and every dispatched
# job sat "Waiting for capacity…"). The image already carries the committed
# backend at /root/backend, so /root is the in-container _HEAD.

_BASE_IMAGE = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_pyproject(
        os.path.join(_HEAD, "backend", "pyproject.toml"),
    )
    .pip_install("pyyaml>=6.0", "jsonschema>=4.20")
)

if modal.is_local():
    image = (
        _BASE_IMAGE
        # eval-agent runtime (hmo_item_verify executor, W-249): tiny deps the
        # backend image may not carry.
        .add_local_dir(
            os.path.join(_HEAD, "backend"),
            remote_path="/root/backend",
            copy=True,
        )
        .add_local_dir(
            os.path.join(_HEAD, "backend", "ontology"),
            remote_path="/root/ontology",
            copy=True,
        )
        .add_local_dir(
            os.path.join(_HEAD, "eval-agent"),
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
else:
    # In-container import: the image is already materialized (paths baked
    # in above at deploy time). Rebuild only a metadata shell for the
    # decorators; never touch add_local_dir inside a container.
    image = _BASE_IMAGE.env({
        "PYTHONPATH": "/root/backend:/root/eval-agent",
        "EVAL_AGENT_ROOT": "/root/eval-agent",
    })

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
                # W-254: the executor bumps its own liveness column. The web
                # owner heartbeats ``updated_at`` for ``modal-%`` rows while
                # polling, so executor staleness must never be read from
                # ``updated_at`` — a preemption restart would otherwise see a
                # permanently fresh claim and exit (2026-09-21 zombie).
                await db.execute(
                    update(RunJob)
                    .where(
                        RunJob.id == job_id,
                        RunJob.status == "running",
                        RunJob.claimed_by == executor_id,
                    )
                    .values(
                        updated_at=datetime.now(timezone.utc),
                        executor_heartbeat_at=datetime.now(timezone.utc),
                    )
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

        from sqlalchemy import func, select, update

        from app import db as app_db
        from app.db import session_scope
        from app.models.run_job import RunJob

        # Warm-container reuse across job invocations gives each
        # asyncio.run() a fresh loop; the engine pool from a previous
        # invocation carries stale-loop futures. Reset before any query.
        await app_db.reset_engine()

        executor_id = f"modal-executor:{_uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        stale = now - timedelta(seconds=120)

        # Exclusive lease: exactly one container may run this job. A
        # preemption restart re-enters here while the dead container's
        # claim may still look fresh — sleep ONCE until its heartbeat
        # would have gone stale (no busy waiting), then take over.
        # Staleness reads the executor's own liveness column (W-254): the
        # web owner heartbeats ``updated_at`` for ``modal-%`` rows while
        # polling, so a dead executor's claim would otherwise look fresh
        # forever and the restart would exit without acquiring (2026-09-21
        # zombie). Legacy rows without the column fall back to
        # ``updated_at`` via COALESCE.
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
                                & (func.coalesce(RunJob.executor_heartbeat_at, RunJob.updated_at) < stale)
                            )
                        ),
                    )
                    .values(
                        claimed_by=executor_id,
                        updated_at=datetime.now(timezone.utc),
                        executor_heartbeat_at=datetime.now(timezone.utc),
                    )
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
                # Batch-build (R23): fan the corpus out to parallel shard
                # containers; the claimed container orchestrates progress +
                # terminal state on the single claimed job row.
                await _run_rdf_build_sharded(job_id)
            elif kind == "hmo_item_build":
                from app.pipeline.hmo_item_build_job import run_hmo_item_build_job

                await run_hmo_item_build_job(job_id)
            elif kind == "hmo_item_verify":
                # W-249: the eval-agent subprocess + 18k-item scope need the
                # container's 8 GB — the 512 MB web dyno thrashed (R14).
                from app.pipeline.verify_job import run_verify_job

                await run_verify_job(job_id)
            elif kind == "hmo_rule_verify":
                # Sharded fan-out: parallel rule-check containers; the
                # claimed container orchestrates progress + terminal state.
                await _run_rule_verify_sharded(job_id)
            elif kind == "wikidata_studio_build":
                # Sharded fan-out (batch-build parity with rdf_build):
                # parallel item-builder containers; the claimed container
                # merges, finishes the corpus, and owns terminal state.
                await _run_wikidata_studio_build_sharded(job_id)
            else:
                raise ValueError(f"kind {kind!r} has no Modal executor")
        except Exception as exc:  # noqa: BLE001
            # A dead runner must never leave a zombie: fail the row HERE so
            # the web-side waiter sees a terminal state (2026-09-18: the
            # verify container died on an eval-agent ImportError and the
            # row ran "forever" on web-side heartbeats, Rule W-249).
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


# ── rule-verify shard fan-out (batch-build R23 parity) ───────────────────
# The 18k-item rule check fans out to parallel shard containers: each
# loads the merged scope slice, runs the deterministic CPU engine,
# persists its slice's rule_verdicts, and returns a partial summary
# (Rule W-256: CPU rules only — the Wikidata probes run once, in the
# dedicated run_rule_verify_api_pass container). The orchestrator (below,
# inside the claimed container) merges summaries and owns progress +
# terminal state on the single claimed job row.

# Prod Postgres is a shared essential-1 instance with a hard 20-connection
# budget that other tenants fluctuate (Rule W-253): every shard container
# holds its own SQLAlchemy pool, so the fan-out is env-tunable. Defaults
# sized so peak shard pools + the claimed orchestrator + web dynos fit
# under the budget; set MHM_RULE_VERIFY_SHARD_SIZE / MHM_RDF_BUILD_SHARD_SIZE
# on the mhm-jobs2 secret to retune without a code change.
_RULE_VERIFY_SHARD_SIZE = int(os.environ.get("MHM_RULE_VERIFY_SHARD_SIZE") or 6000)


@app.function(
    image=image,
    cpu=1,
    memory=4096,
    timeout=7200,
    secrets=[modal.Secret.from_name("mhm-jobs2")],
)
def run_rule_verify_shard(job_id: str, run_id: str, local_ids: list[str]) -> dict:
    import asyncio  # noqa: PLC0415
    import uuid as _uuid

    from app.pipeline.rule_verify_job import run_rule_verify_shard as run_shard

    async def _run() -> dict:
        # Warm containers run this function once per shard invocation;
        # each asyncio.run makes a fresh loop, so the engine pool from a
        # previous invocation carries stale-loop futures. Reset first.
        from app import db as app_db

        await app_db.reset_engine()
        return await run_shard(job_id, _uuid.UUID(run_id), local_ids)

    return asyncio.run(_run())


# Rule W-256: the Wikidata probes run in ONE container so the Action API
# stays a single throttle domain (Rule W-139) with one shared budget.
# Timeout vs budget: ~30k probes x ~1.15 s (1.1 s throttle + latency) ≈
# 9.5 h worst case — raise this together with RULE_VERIFY_API_PASS_PROBE_MAX.
_API_PASS_TIMEOUT_S = 21600  # 6 h


@app.function(
    image=image,
    cpu=1,
    memory=8192,
    timeout=_API_PASS_TIMEOUT_S,
    secrets=[modal.Secret.from_name("mhm-jobs2")],
)
def run_rule_verify_api_pass(job_id: str, run_id: str) -> dict:
    import asyncio  # noqa: PLC0415
    import uuid as _uuid  # noqa: PLC0415

    from app.pipeline.rule_verify_job import (
        run_rule_verify_api_pass as run_api_pass,
    )

    async def _run() -> dict:
        # Warm containers: reset the engine pool the same way the shard
        # function does (fresh loop per invocation).
        from app import db as app_db

        await app_db.reset_engine()
        return await run_api_pass(job_id, _uuid.UUID(run_id))

    return asyncio.run(_run())


async def _load_rule_verify_plan(
    job_id: str,
    should_cancel: Any = None,
) -> tuple[str, list[str], dict] | None:
    import uuid as _uuid

    from sqlalchemy import select

    from app.db import session_scope
    from app.models.run_job import RunJob
    from app.pipeline.rule_verify.scope import (
        ItemBuildMissingError,
        load_rule_verify_scope,
    )

    async with session_scope() as db:
        job = (
            await db.execute(select(RunJob).where(RunJob.id == job_id))
        ).scalar_one_or_none()
        if job is None or job.status != "running":
            return None
        run_id = str(job.run_id)
        params = dict(job.params or {})
    item_ids = [str(x) for x in (params.get("item_ids") or [])] or None
    try:
        async with session_scope() as db:
            items = await load_rule_verify_scope(
                db, _uuid.UUID(run_id), item_ids=item_ids,
                should_cancel=should_cancel,
            )
    except ItemBuildMissingError:
        raise ValueError(f"no item build for run {run_id}") from None
    local_ids = [str(i.get("local_id") or "") for i in items if i.get("local_id")]
    return run_id, local_ids, params


def _shards(local_ids: list[str], size: int) -> list[list[str]]:
    return [local_ids[i : i + size] for i in range(0, len(local_ids), size)] or [[]]


async def _is_cancel_requested(job_id: str) -> bool:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models.run_job import RunJob

    async with session_scope() as db:
        row = (
            await db.execute(select(RunJob).where(RunJob.id == job_id))
        ).scalar_one_or_none()
    return bool(row is not None and row.cancel_requested_at is not None)


async def _update_rule_verify_progress(
    job_id: str, done: int, total: int, message: str | None = None,
) -> None:
    from sqlalchemy import update

    from app.db import session_scope
    from app.models.run_job import RunJob

    async with session_scope() as db:
        await db.execute(
            update(RunJob)
            .where(RunJob.id == job_id)
            .values(
                progress={
                    "phase": "running", "processed": done, "total": total,
                    "message": message or f"Checked {done} of {total} items…",
                },
                updated_at=datetime.now(timezone.utc),
            )
            .execution_options(synchronize_session=False),
        )
        await db.commit()


async def _finalise_rule_verify(job_id: str, summary: dict, total: int, cancelled: bool) -> None:
    from sqlalchemy import update

    from app.db import session_scope
    from app.models.run_job import (
        JOB_STATUS_CANCELLED,
        JOB_STATUS_SUCCEEDED,
        RunJob,
    )

    status = JOB_STATUS_CANCELLED if cancelled else JOB_STATUS_SUCCEEDED
    async with session_scope() as db:
        await db.execute(
            update(RunJob)
            .where(RunJob.id == job_id, RunJob.status == "running")
            .values(
                status=status,
                error="Cancelled by user" if cancelled else None,
                result=summary,
                finished_at=datetime.now(timezone.utc),
                progress={
                    "phase": "cancelled" if cancelled else "done",
                    "processed": summary.get("scope", 0),
                    "total": total,
                    "message": (
                        "Cancelled by user"
                        if cancelled else f"Rule check complete: {total} items"
                    ),
                },
                updated_at=datetime.now(timezone.utc),
            )
            .execution_options(synchronize_session=False),
        )
        await db.commit()


async def _run_rule_verify_sharded(job_id: str) -> None:
    """Orchestrate the shard fan-out from inside the claimed container."""
    import asyncio as _asyncio
    import queue as _queue
    import threading as _threading
    import uuid as _uuid

    from app.pipeline.run_job_service import (
        JobCancelledError,
        cancel_watcher,
        update_job_progress,
    )

    should_cancel = cancel_watcher(_uuid.UUID(job_id))

    # The scope load can run for minutes on big runs; publish preparing
    # progress FIRST so the tray shows the phase instead of a bare
    # "running", and let the watcher abort the load on Cancel (Rule R28).
    await update_job_progress(_uuid.UUID(job_id), {
        "phase": "preparing", "processed": 0, "total": 0,
        "message": "Loading rule-verify scope…",
    })
    if await _is_cancel_requested(job_id):
        await _finalise_rule_verify(
            job_id, {"scope": 0, "overall_counts": {}, "per_rule": {}}, 0, True,
        )
        return
    try:
        plan = await _load_rule_verify_plan(job_id, should_cancel=should_cancel)
    except JobCancelledError:
        await _finalise_rule_verify(
            job_id, {"scope": 0, "overall_counts": {}, "per_rule": {}}, 0, True,
        )
        return
    if plan is None:
        return
    run_id, local_ids, params = plan
    with_api = bool(params.get("with_api", True))
    total = len(local_ids)
    if not total:
        await _finalise_rule_verify(
            job_id,
            {"scope": 0, "overall_counts": {}, "per_rule": {}}, 0, False,
        )
        return
    await _update_rule_verify_progress(job_id, 0, total)
    if await _is_cancel_requested(job_id):
        await _finalise_rule_verify(
            job_id, {"scope": 0, "overall_counts": {}, "per_rule": {}}, total, True,
        )
        return
    shards = _shards(local_ids, _RULE_VERIFY_SHARD_SIZE)

    # modal's starmap iterator is synchronous — blocking the event loop
    # here would starve the heartbeat task (W-244 stale reap). Collect
    # shard results on a thread; the loop consumes with a timeout so the
    # cancel flag stays responsive.
    results_q: _queue.Queue = _queue.Queue()

    def _consume() -> None:
        try:
            for res in run_rule_verify_shard.starmap(
                [(job_id, run_id, shard) for shard in shards],
            ):
                results_q.put(res)
        except Exception as exc:  # noqa: BLE001 — surfaced below
            results_q.put({"__error__": str(exc)})
        results_q.put(None)

    _threading.Thread(target=_consume, daemon=True).start()

    summaries: list[dict] = []
    done = 0
    cancelled = False
    while True:
        try:
            item = await _asyncio.to_thread(results_q.get, True, 10)
        except _queue.Empty:
            if await _is_cancel_requested(job_id):
                cancelled = True
                break
            continue
        if item is None:
            break
        if isinstance(item, dict) and item.get("__error__"):
            raise RuntimeError(f"rule-verify shard failed: {item['__error__']}")
        summaries.append(item or {})
        done += _RULE_VERIFY_SHARD_SIZE
        await _update_rule_verify_progress(job_id, min(done, total), total)
        if await _is_cancel_requested(job_id):
            cancelled = True
            break

    # Rule W-256: shards ran CPU rules only. The Wikidata probes run once,
    # in a single dedicated container — one throttle domain (W-139), one
    # shared budget — and merge their results into the shard verdicts.
    if not cancelled and with_api:
        await _update_rule_verify_progress(
            job_id, min(done, total), total,
            "Probing live Wikidata labels…",
        )
        summaries.append(await _dispatch_rule_verify_api_pass(job_id, run_id))
        if await _is_cancel_requested(job_id):
            cancelled = True

    from app.pipeline.rule_verify_job import merge_summaries

    await _finalise_rule_verify(
        job_id, merge_summaries(summaries), total, cancelled,
    )


async def _dispatch_rule_verify_api_pass(job_id: str, run_id: str) -> dict:
    """Run the single-container API pass; degrade inline on failure (W-15).

    The dedicated container carries the 6 h probe budget/timeout pair; the
    orchestrator container (12 h) can absorb the pass itself when Modal
    dispatch fails, so a broken fan-out never silently skips the probes.
    """
    import uuid as _uuid

    try:
        return await _asyncio.to_thread(run_rule_verify_api_pass.call, job_id, run_id)
    except Exception as exc:  # noqa: BLE001 — degraded inline pass beats no pass
        logger.warning(
            "rule-verify api pass dispatch failed; running inline: %s", exc,
        )
        from app.pipeline.rule_verify_job import (
            run_rule_verify_api_pass as run_api_pass,
        )

        return await run_api_pass(job_id, _uuid.UUID(run_id))


# ── rdf_build shard fan-out (batch-build R23) ────────────────────────────
# The RDF corpus fans out to parallel shard containers: each maps its
# control_number slice with the same streaming mapper and returns the
# serialized Turtle chunk. The claimed container orchestrates: it appends
# chunks to the artifact in control_number order, keeps byte-offset
# checkpoints identical to the sequential path (a local fallback resumes
# at the same record index), and owns progress + terminal state.

_RDF_BUILD_SHARD_SIZE = int(os.environ.get("MHM_RDF_BUILD_SHARD_SIZE") or 4000)


@app.function(
    image=image,
    cpu=1,
    memory=4096,
    timeout=7200,
    secrets=[modal.Secret.from_name("mhm-jobs2")],
)
def run_rdf_build_shard(job_id: str, run_id: str, control_numbers: list[str]) -> dict:
    import asyncio  # noqa: PLC0415
    import uuid as _uuid  # noqa: PLC0415

    from app.pipeline.rdf_build_shard import run_rdf_build_shard as run_shard

    async def _run() -> dict:
        # Warm containers run this function once per shard invocation;
        # each asyncio.run makes a fresh loop, so the engine pool from a
        # previous invocation carries stale-loop futures. Reset first.
        from app import db as app_db

        await app_db.reset_engine()
        return await run_shard(_uuid.UUID(job_id), _uuid.UUID(run_id), control_numbers)

    return asyncio.run(_run())


async def _rdf_shard_results(job_id: str, run_id: str, slices: list[list[str]]):
    """Ordered async stream over the shard ``starmap`` (input order).

    Modal's starmap iterator is synchronous — blocking the event loop
    would starve the heartbeat task (W-244 stale reap). Collect results
    on a thread; the loop consumes with a timeout so the cancel flag
    stays responsive.
    """
    import asyncio as _asyncio
    import queue as _queue
    import threading as _threading

    results_q: _queue.Queue = _queue.Queue()

    def _consume() -> None:
        try:
            for res in run_rdf_build_shard.starmap(
                [(job_id, run_id, cns) for cns in slices],
            ):
                results_q.put(res)
        except Exception as exc:  # noqa: BLE001 — surfaced by the consumer
            results_q.put({"__error__": str(exc)})
        results_q.put(None)

    _threading.Thread(target=_consume, daemon=True).start()

    while True:
        try:
            item = await _asyncio.to_thread(results_q.get, True, 10)
        except _queue.Empty:
            if await _is_cancel_requested(job_id):
                yield {"__cancelled__": True}
                return
            continue
        if item is None:
            return
        yield item


async def _run_rdf_build_sharded(job_id: str) -> None:
    """Orchestrate the RDF build shard fan-out from the claimed container."""
    import uuid as _uuid

    from app.pipeline.rdf_build_shard import consume_rdf_shard_results, load_rdf_shard_plan

    plan = await load_rdf_shard_plan(_uuid.UUID(job_id), _RDF_BUILD_SHARD_SIZE)
    if plan is None:
        return
    job_uuid = _uuid.UUID(job_id)

    async def _results():
        async for item in _rdf_shard_results(
            job_id, str(plan.run_id), [cns for _start, cns in plan.slices],
        ):
            yield item

    await consume_rdf_shard_results(job_uuid, plan, _results())


# ── wikidata_studio_build shard fan-out (batch-build parity) ─────────────
# The Studio item corpus fans out to parallel builder containers: each
# maps its control_number slice with the same desktop builder and
# returns lossless native item payloads. The claimed container merges
# (persons/works dedupe by the builder's local_id key), runs the
# corpus-wide finish pipeline once, gates the result, upserts the same
# cache row the sequential build writes, and owns progress + terminal
# state. Memory profile: no process holds the MARC/authority corpus —
# shards hold one slice, the orchestrator holds merged native items.

_WIKIDATA_BUILD_SHARD_SIZE = 500


@app.function(
    image=image,
    cpu=2,
    memory=8192,
    timeout=7200,
    secrets=[modal.Secret.from_name("mhm-jobs2")],
)
def run_wikidata_studio_build_shard(
    job_id: str, run_id: str, control_numbers: list[str],
) -> dict:
    import asyncio  # noqa: PLC0415
    import uuid as _uuid  # noqa: PLC0415

    from app.pipeline.wikidata_studio_build_shard import (
        run_wikidata_studio_build_shard as run_shard,
    )

    async def _run() -> dict:
        # Warm containers run this function once per shard invocation;
        # each asyncio.run makes a fresh loop, so the engine pool from a
        # previous invocation carries stale-loop futures. Reset first.
        from app import db as app_db

        await app_db.reset_engine()
        return await run_shard(
            _uuid.UUID(job_id), _uuid.UUID(run_id), control_numbers,
        )

    return asyncio.run(_run())


async def _wikidata_shard_results(job_id: str, run_id: str, slices: list[list[str]]):
    """Ordered async stream over the shard ``starmap`` (input order)."""
    import asyncio as _asyncio
    import queue as _queue
    import threading as _threading

    results_q: _queue.Queue = _queue.Queue()

    def _consume() -> None:
        try:
            for res in run_wikidata_studio_build_shard.starmap(
                [(job_id, run_id, cns) for cns in slices],
            ):
                results_q.put(res)
        except Exception as exc:  # noqa: BLE001 — surfaced by the consumer
            results_q.put({"__error__": str(exc)})
        results_q.put(None)

    _threading.Thread(target=_consume, daemon=True).start()

    while True:
        try:
            item = await _asyncio.to_thread(results_q.get, True, 10)
        except _queue.Empty:
            if await _is_cancel_requested(job_id):
                yield {"__cancelled__": True}
                return
            continue
        if item is None:
            return
        yield item


async def _run_wikidata_studio_build_sharded(job_id: str) -> None:
    """Orchestrate the Studio item build shard fan-out."""
    import uuid as _uuid

    from app.models.run_job import JOB_STATUS_CANCELLED
    from app.pipeline.run_job_service import (
        JobCancelledError,
        cancel_watcher,
        finish_job,
    )
    from app.pipeline.wikidata_studio_build_shard import (
        consume_wikidata_shard_results,
        load_wikidata_shard_plan,
    )

    should_cancel = cancel_watcher(_uuid.UUID(job_id))
    try:
        plan = await load_wikidata_shard_plan(
            _uuid.UUID(job_id), _WIKIDATA_BUILD_SHARD_SIZE,
            should_cancel=should_cancel,
        )
    except JobCancelledError:
        # The fingerprint/cache plan load can run for minutes; finalize at
        # the flag instead of crawling to the end of the load (Rule R28).
        await finish_job(
            _uuid.UUID(job_id),
            status=JOB_STATUS_CANCELLED,
            error="Cancelled by user",
            progress={
                "phase": "cancelled", "processed": 0, "total": 1,
                "unit": "records", "message": "Cancelled by user",
            },
        )
        return
    if plan is None:
        return

    async def _results():
        async for item in _wikidata_shard_results(
            job_id, str(plan.run_id), plan.slices,
        ):
            yield item

    await consume_wikidata_shard_results(plan, _results(), should_cancel=should_cancel)


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
    if not job_id or kind not in (
        "rdf_build", "hmo_item_build", "hmo_item_verify", "hmo_rule_verify",
        "wikidata_studio_build",
    ):
        raise HTTPException(status_code=422, detail="job_id and kind required")

    # Row must be running AND hold the dispatch lease (the Heroku client
    # sets claimed_by='modal-dispatch' right before calling us — this is
    # what makes double dispatch after a web restart harmless).
    import asyncio

    async def _check() -> tuple[str | None, str | None]:
        from sqlalchemy import select

        from app import db as app_db
        from app.db import session_scope
        from app.models.run_job import RunJob

        # Same warm-container loop hazard as the runners (see _execute).
        await app_db.reset_engine()

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
