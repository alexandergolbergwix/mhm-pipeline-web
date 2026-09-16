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
_TIMEOUT_S = 7200

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_pyproject(
        os.path.join(_ROOT, "backend", "pyproject.toml"),
    )
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
    .env({"PYTHONPATH": "/root/backend"})
)

app = modal.App(
    "mhm-jobs",
    secrets=[modal.Secret.from_name("mhm-jobs")],
)


def _authorize(authorization: str | None) -> bool:
    import hmac

    expected = os.environ.get("MODAL_JOBS_TOKEN", "")
    if not expected:
        return False
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    return hmac.compare_digest(supplied, expected)


def _run_job_detached(job_id: str, kind: str) -> dict:
    """Container entry: run the exact Heroku job runner for one claimed row."""
    import asyncio

    async def _execute() -> None:
        # Verify the row is still claimed + running (web is the arbiter).
        from sqlalchemy import select

        from app.db import session_scope
        from app.models.run_job import RunJob

        async with session_scope() as db:
            job = (
                await db.execute(select(RunJob).where(RunJob.id == job_id))
            ).scalar_one_or_none()
        if job is None or job.status != "running":
            return  # cancelled / already finalized while we cold-started

        if kind == "rdf_build":
            from app.pipeline.rdf_build_job import run_rdf_build_job

            await run_rdf_build_job(job_id)
        elif kind == "hmo_item_build":
            from app.pipeline.hmo_item_build_job import run_hmo_item_build_job

            await run_hmo_item_build_job(job_id)
        else:
            raise ValueError(f"kind {kind!r} has no Modal executor")

    asyncio.run(_execute())
    return {"ok": True, "job_id": job_id, "kind": kind}


@app.function(
    image=image,
    cpu=2,
    memory=8192,
    timeout=_TIMEOUT_S,
    secrets=[modal.Secret.from_name("mhm-jobs")],
)
def run_modal_job_detached(job_id: str, kind: str) -> dict:
    return _run_job_detached(job_id, kind)


@app.function(
    image=image,
    cpu=1,
    memory=512,
    scaledown_window=300,
    secrets=[modal.Secret.from_name("mhm-jobs")],
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
    if not job_id or kind not in ("rdf_build", "hmo_item_build"):
        raise HTTPException(status_code=422, detail="job_id and kind required")

    # Row must be running (web already claimed it under the heavy cap).
    import asyncio

    async def _check() -> str | None:
        from sqlalchemy import select

        from app.db import session_scope
        from app.models.run_job import RunJob

        async with session_scope() as db:
            job = (
                await db.execute(select(RunJob).where(RunJob.id == job_id))
            ).scalar_one_or_none()
        return str(job.status) if job is not None else None

    status = asyncio.run(_check())
    if status != "running":
        raise HTTPException(
            status_code=409,
            detail=f"job {job_id} is {status or 'missing'}, expected running",
        )

    run_modal_job_detached.spawn(job_id, kind)
    return {"ok": True, "spawned": True}
