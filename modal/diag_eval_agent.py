"""Diagnostic: run eval-agent end-to-end on a tiny inline fixture.

Usage: modal run modal/diag_eval_agent.py
Prints the subprocess stdout/stderr lines prefixed DIAGPROC so the exact
failure point of the 0-verdict issue is visible in container logs.
"""
import os

import modal

_ROOT = os.path.dirname(os.path.abspath(__file__))

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_pyproject(os.path.join(_ROOT, "..", "backend", "pyproject.toml"))
    .pip_install("pyyaml>=6.0", "jsonschema>=4.20")
    .add_local_dir(os.path.join(_ROOT, "..", "backend"), remote_path="/root/backend", copy=True)
    .add_local_dir(os.path.join(_ROOT, "..", "backend", "ontology"), remote_path="/root/ontology", copy=True)
    .add_local_dir(os.path.join(_ROOT, "..", "eval-agent"), remote_path="/root/eval-agent", copy=True)
    .env({
        "PYTHONPATH": "/root/backend:/root/eval-agent",
        "EVAL_AGENT_ROOT": "/root/eval-agent",
    })
)

app = modal.App("mhm-eval-diag", image=image,
                secrets=[modal.Secret.from_name("mhm-jobs2")])


@app.function(cpu=2, memory=8192, timeout=3600)
def run_diag() -> dict:
    import asyncio
    import json
    import os
    from pathlib import Path

    root = "/root/eval-agent"
    state_dir = Path("/tmp/eval-diag")
    sess = state_dir / "sessions" / "diag"
    fixture = sess / "pipeline-output"
    fixture.mkdir(parents=True, exist_ok=True)

    # REAL fixture: pull items + MARC from prod DB exactly like the verify job.
    async def _fetch():
        from app.db import session_scope
        from app.routers.hmo_studio_items import _fetch_verify_items, _load_marc_records
        rid = os.environ["DIAG_RUN_ID"]
        async with session_scope() as db:
            items = await _fetch_verify_items(db, rid, item_ids=None)
            marc = await _load_marc_records(db, rid)
        return items, marc

    items, marc = asyncio.run(_fetch())
    print(f"DIAG fetched items={len(items)} marc={len(marc)}", flush=True)
    (fixture / "hmo_wikibase_items.json").write_text(json.dumps(items, ensure_ascii=False))
    (fixture / "marc_extracted.json").write_text(json.dumps(marc, ensure_ascii=False))
    print(f"DIAG fixture bytes={fixture.stat().st_size}", flush=True)

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["EVAL_AGENT_STATE_DIR"] = str(state_dir)
    env["PYTHONPATH"] = root
    cmd = [
        "/usr/local/bin/python3.12", "-m", "eval_agent.cli", "run",
        "--pipeline-output", str(fixture),
        "--evaluators", "hmo_wikibase_item",
        "--rpm", "60",
        "--no-self-verify",
        "--state-dir", str(state_dir),
        "--tier-model", "deepseek-ai/DeepSeek-V4-Flash",
        "--parallel", "4",
    ]
    print(f"DIAG cmd={cmd[2:]}", flush=True)

    async def _run() -> None:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=root, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )

        async def pump(stream, tag):
            while True:
                line = await stream.readline()
                if not line:
                    return
                print(f"DIAGPROC {tag}: {line.decode(errors='replace').rstrip()}",
                      flush=True)

        t1 = asyncio.create_task(pump(proc.stdout, "OUT"))
        t2 = asyncio.create_task(pump(proc.stderr, "ERR"))
        try:
            await asyncio.wait_for(proc.wait(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            print("DIAGPROC: killed after 300s", flush=True)
        await asyncio.gather(t1, t2, return_exceptions=True)
        runs = state_dir / "runs"
        found = list(runs.rglob("results.jsonl")) if runs.exists() else []
        print(f"DIAGPROC exit={proc.returncode} results_files={len(found)}",
              flush=True)
        for f in found[:1]:
            print(f"DIAGPROC sample: {f.read_text()[:400]}", flush=True)

    asyncio.run(_run())
    return {"ok": True}
