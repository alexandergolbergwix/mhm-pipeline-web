#!/usr/bin/env python3
"""Measurement-only local rebuild + AI re-verify for one run.

Runs the curator-ops loop — **rebuild artifacts → re-verify** — entirely on
the local machine with the current (possibly undeployed) code, **without
writing anything back to the database**. It reads a run's data from the
configured ``DATABASE_URL`` (Heroku prod or local) *read-only*, builds the
Studio items in a local scratch dir, re-judges a chosen scope of items with an
eval-agent tier-1 model (Qubrid Kimi K2.5 by default), and writes a
before/after verdict report.

Use it to measure the true post-fix baseline of an RDF / label / rubric change
*before* deploying — e.g. Rule W-53 (HMO items) or a Wikidata Studio fix — so
you can see how many previously-``partial``/``fail`` items now pass without
touching production caches, verdicts, or the live wiki.

Channels (``--channel``)
------------------------
- ``hmo``       — HMO Wikibase Studio items. Build = RDF rebuild
                  (``build_rdf_graph``) → ``HmoWikibaseExporter`` →
                  ``resolve_against_mappings`` → SHACL. Evaluator
                  ``hmo_wikibase_item``.
- ``wikidata``  — Wikidata Studio items. Build = ``wikidata_studio.
                  build_items_for_run`` (already DB-free). Evaluator
                  ``wikidata_item``.

Both build paths take their inputs from the DB read-only and never upsert a
cache row, so nothing in production changes.

Scope (``--scope``)
-------------------
- ``non-passing`` (default) — items whose *baseline* verdict ``overall`` is
  ``partial`` or ``fail``. Baseline comes from ``--baseline-export`` when
  given, otherwise from the run's stored verdicts (read-only).
- ``all`` — every built item.
- Plus ``--local-id`` (repeatable) and ``--limit`` for ad-hoc slices.

Environment
-----------
- ``DATABASE_URL`` — Postgres (Heroku prod or local). Read-only here.
- ``QUBRID_API_KEY`` (alias ``QUABRID_API_KEY``) — Qubrid OpenAI-compatible
  key; falls back to ``heroku config:get QUBRID_API_KEY -a mhm-pipeline-web``.
- ``GEMINI_API_KEY`` — only needed when ``--tier-model`` is a Gemini model.
- A repo-root ``.env`` is loaded when present.

Examples
--------
Measure the HMO post-W-53 baseline on the previously-failing scope::

    cd backend
    DATABASE_URL=... python -m scripts.local_measure_verify \\
        --channel hmo --run-id 48ba6c13-115c-4763-bff1-c08b9031b518 \\
        --baseline-export "~/Downloads/run-48ba6c13-…-hmo-wikibase-items (5).json"

A quick 5-item pilot::

    python -m scripts.local_measure_verify --channel hmo \\
        --run-id 48ba6c13-115c-4763-bff1-c08b9031b518 --limit 5

Wikidata Studio, all items::

    python -m scripts.local_measure_verify --channel wikidata \\
        --run-id <run_id> --scope all
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

_BACKEND = Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.pipeline.agent_runner import _python_for, locate_eval_agent  # noqa: E402
from app.pipeline.marc_verify_context import (  # noqa: E402
    attach_marc_context,
    load_run_marc_records,
)

DEFAULT_RUN_ID = "48ba6c13-115c-4763-bff1-c08b9031b518"
DEFAULT_TIER_MODEL = "moonshotai/Kimi-K2.5"
BAD_OVERALLS = ("partial", "fail")


# ── env helpers ───────────────────────────────────────────────────────────
def _load_dotenv() -> None:
    env_path = _REPO / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _resolve_tier_key(tier_model: str) -> tuple[str, str | None]:
    """Return ``(env_var_name, key)`` for the chosen tier model's provider."""
    from app.pipeline.judge_models import resolve_tier1_model  # noqa: PLC0415

    spec = resolve_tier1_model(tier_model)
    env_name = spec.api_key_env
    for name in (env_name, "QUABRID_API_KEY" if env_name == "QUBRID_API_KEY" else env_name):
        val = os.environ.get(name, "").strip()
        if val:
            os.environ[env_name] = val
            return env_name, val
    if env_name == "QUBRID_API_KEY":
        try:
            out = subprocess.run(
                ["heroku", "config:get", "QUBRID_API_KEY", "-a", "mhm-pipeline-web"],
                capture_output=True, text=True, check=False, timeout=30,
            )
            val = (out.stdout or "").strip()
            if val and not val.startswith("Error"):
                os.environ[env_name] = val
                return env_name, val
        except (OSError, subprocess.TimeoutExpired):
            pass
    return env_name, None


# ── channel adapters ──────────────────────────────────────────────────────
class Channel:
    """One Studio channel: how to build items + read a baseline, read-only."""

    name: str
    evaluator: str
    items_filename: str

    async def build_measurement(
        self, run_id: uuid.UUID, scratch: Path,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return ``(items, marc_records)`` — no DB writes."""
        raise NotImplementedError

    async def baseline_from_db(self, run_id: uuid.UUID) -> dict[str, dict[str, Any]]:
        """Return ``{local_id: verdict}`` from stored overrides (read-only)."""
        raise NotImplementedError


class HmoChannel(Channel):
    name = "hmo"
    evaluator = "hmo_wikibase_item"
    items_filename = "hmo_wikibase_items.json"

    async def build_measurement(self, run_id, scratch):
        from app.models.extraction_approval import ExtractionApproval
        from app.models.run import AuthorityMatch, RdfTripleOverride, RunRecord
        from app.pipeline import hmo_item_build
        from app.pipeline.hmo_item_shacl import build_shacl_report_for_items
        from app.pipeline.rdf_build import (
            RdfBuildOptions,
            build_rdf_graph,
            normalise_matches,
        )
        from converter.wikibase.hmo_exporter import (
            HmoWikibaseExporter,
            resolve_against_mappings,
        )
        from app.pipeline.hmo_export_quality_gate import assert_export_quality
        from fastapi.concurrency import run_in_threadpool

        async with session_scope() as db:
            records = (await db.execute(
                select(RunRecord).where(RunRecord.run_id == run_id)
                .order_by(RunRecord.control_number.asc())
            )).scalars().all()
            if not records:
                raise SystemExit(f"run {run_id} has no MARC records")
            matches = (await db.execute(
                select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
                .where(AuthorityMatch.approved.is_(True))
            )).scalars().all()
            ner_rows = (await db.execute(
                select(ExtractionApproval).where(ExtractionApproval.run_id == run_id)
                .where(ExtractionApproval.approved.is_(True))
            )).scalars().all()
            overrides_rows = (await db.execute(
                select(RdfTripleOverride).where(RdfTripleOverride.run_id == run_id)
            )).scalars().all()
            schema_mappings = await hmo_item_build._load_schema_mappings(db)

            marc_records = [dict(r.marc) for r in records]
            authority_matches = normalise_matches(matches)
            entities_by_cn: dict[str, list[dict[str, Any]]] = {}
            for r in ner_rows:
                entities_by_cn.setdefault(r.control_number, []).append({
                    "text": r.override_text or r.text,
                    "type": (r.override_type or r.type or "").upper(),
                    "role": (r.override_role or r.role or "").upper(),
                    "source": r.source,
                    "start": int(r.start or 0),
                    "end": int(r.end or 0),
                    "confidence": r.confidence,
                    "model_confidence": r.model_confidence,
                })
            kima_places_by_cn: dict[str, dict[str, str]] = {}
            for rec in marc_records:
                cn = str(rec.get("_control_number") or rec.get("control_number") or "")
                kp = rec.get("kima_places")
                if cn and isinstance(kp, dict) and kp:
                    kima_places_by_cn[cn.strip("\"'")] = kp
            overrides = [{
                "subject_uri": r.subject_uri,
                "predicate_uri": r.predicate_uri,
                "new_value": r.new_value,
                "new_datatype": r.new_datatype,
                "new_lang": r.new_lang,
            } for r in overrides_rows]

        ttl_path = scratch / "manuscripts.ttl"
        result = await build_rdf_graph(
            marc_records=marc_records,
            authority_matches=authority_matches,
            entities_by_cn=entities_by_cn,
            output_path=ttl_path,
            overrides=overrides,
            kima_places_by_cn=kima_places_by_cn,
            build_options=RdfBuildOptions(
                add_epistemological_status=True,
                add_cataloging_view=True,
                add_philological_overlay=True,
            ),
        )
        print(f"[build] RDF: {result.manuscripts_count} manuscripts, "
              f"{result.triples_count} triples -> {ttl_path}")

        drafts = await run_in_threadpool(HmoWikibaseExporter().from_ttl, ttl_path)
        await run_in_threadpool(assert_export_quality, drafts)
        resolved = await run_in_threadpool(
            resolve_against_mappings, drafts, schema_mappings,
        )
        resolved_dicts = [e.to_dict() for e in resolved]
        shacl_report = await build_shacl_report_for_items(ttl_path, resolved_dicts)
        items: list[dict[str, Any]] = []
        for e in resolved_dicts:
            lid = str(e.get("local_id") or "")
            items.append({**e, "shacl_issues": shacl_report.get(lid) or []})
        print(f"[build] HMO items: {len(items)} entities")
        return items, marc_records

    async def baseline_from_db(self, run_id):
        from app.models.hmo_studio_item_override import HmoStudioItemOverride
        async with session_scope() as db:
            rows = (await db.execute(
                select(HmoStudioItemOverride).where(
                    HmoStudioItemOverride.run_id == run_id,
                )
            )).scalars().all()
        return {r.local_id: (r.ai_verdict or {}) for r in rows if r.ai_verdict}


class WikidataChannel(Channel):
    name = "wikidata"
    evaluator = "wikidata_item"
    items_filename = "wikidata_items.json"

    async def build_measurement(self, run_id, scratch):
        from app.models.extraction_approval import ExtractionApproval
        from app.models.run import AuthorityMatch, RunRecord
        from app.pipeline import wikidata_studio

        async with session_scope() as db:
            records = (await db.execute(
                select(RunRecord).where(RunRecord.run_id == run_id)
                .order_by(RunRecord.control_number.asc())
            )).scalars().all()
            if not records:
                raise SystemExit(f"run {run_id} has no MARC records")
            matches = (await db.execute(
                select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
                .where(AuthorityMatch.approved.is_(True))
            )).scalars().all()
            ner_rows = (await db.execute(
                select(ExtractionApproval).where(ExtractionApproval.run_id == run_id)
                .where(ExtractionApproval.approved.is_(True))
            )).scalars().all()
            marc_records = [dict(r.marc) for r in records]
            approved_matches = [{
                "control_number": m.control_number,
                "entity_text": m.entity_text,
                "wikidata_qid": m.wikidata_qid,
                "payload": m.payload or {},
            } for m in matches]
            entities_by_cn: dict[str, list[dict[str, Any]]] = {}
            for r in ner_rows:
                entities_by_cn.setdefault(r.control_number, []).append({
                    "text": r.override_text or r.text,
                    "type": (r.override_type or r.type or "").upper(),
                    "role": (r.override_role or r.role or "").upper(),
                    "source": r.source,
                    "start": int(r.start or 0),
                    "end": int(r.end or 0),
                    "confidence": r.confidence,
                    "model_confidence": r.model_confidence,
                })
            hmo_instance_qids = await wikidata_studio.hmo_instance_qids_for_run(db, run_id)

        built = await wikidata_studio.build_items_for_run(
            marc_records=marc_records,
            approved_matches=approved_matches,
            entities_by_cn=entities_by_cn,
            hmo_instance_qids=hmo_instance_qids,
        )
        items = list(built.get("items") or [])
        for it in items:
            it.setdefault("local_id", wikidata_studio.local_id_for_item(it))
        print(f"[build] Wikidata items: {len(items)} entities")
        return items, marc_records

    async def baseline_from_db(self, run_id):
        from app.models.item_override import WikidataItemOverride
        async with session_scope() as db:
            rows = (await db.execute(
                select(WikidataItemOverride).where(
                    WikidataItemOverride.run_id == run_id,
                )
            )).scalars().all()
        return {
            r.local_id: (r.ai_verdict or {})
            for r in rows if getattr(r, "ai_verdict", None)
        }


CHANNELS: dict[str, Callable[[], Channel]] = {
    "hmo": HmoChannel,
    "wikidata": WikidataChannel,
}


# ── baseline + verdict helpers ────────────────────────────────────────────
def _local_id(item: dict[str, Any]) -> str:
    return str(item.get("local_id") or item.get("_local_id") or "")


def _baseline_from_export(path: Path) -> dict[str, dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else (raw.get("items") or [])
    out: dict[str, dict[str, Any]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        lid = str(it.get("local_id") or it.get("_local_id") or "")
        verdict = it.get("ai_verdict") or it.get("verdict") or {}
        if lid and isinstance(verdict, dict):
            out[lid] = verdict
    return out


def _run_eval_agent(
    *, pipeline_dir: Path, state_dir: Path, evaluator: str,
    tier_model: str, rpm: int,
) -> dict[str, dict[str, Any]]:
    """Run eval-agent over the fixture; return ``{local_id: verdict_row}``."""
    root = locate_eval_agent()
    py = _python_for(root)
    cmd = [
        py, "-m", "eval_agent.cli", "run",
        "--pipeline-output", str(pipeline_dir),
        "--evaluators", evaluator,
        "--linear", "--no-cache", "--no-self-verify",
        "--threshold", "-1",
        "--tier-model", tier_model,
        "--rpm", str(rpm),
        "--state-dir", str(state_dir),
    ]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.run(
        cmd, cwd=str(root), env=env, capture_output=True, text=True,
        timeout=7200, check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        raise RuntimeError(f"eval-agent exited {proc.returncode}: {tail}")

    runs_dir = state_dir / "runs"
    run_dirs = sorted(p for p in runs_dir.iterdir() if p.is_dir()) if runs_dir.is_dir() else []
    if not run_dirs:
        raise RuntimeError("eval-agent produced no run artefacts")
    results_path = run_dirs[-1] / "results.jsonl"
    verdicts: dict[str, dict[str, Any]] = {}
    for line in results_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        cand = row.get("candidate") if isinstance(row.get("candidate"), dict) else {}
        lid = str(cand.get("_local_id") or cand.get("local_id") or row.get("record_id") or "")
        if lid:
            verdicts[lid] = row
    return verdicts


# ── main ──────────────────────────────────────────────────────────────────
async def _async_main(args: argparse.Namespace) -> int:
    _load_dotenv()
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is required (Heroku prod or local .env).")
    env_name, key = _resolve_tier_key(args.tier_model)
    if not key:
        raise SystemExit(f"missing {env_name} for tier model {args.tier_model}")

    channel = CHANNELS[args.channel]()
    run_id = uuid.UUID(args.run_id)
    out_dir = Path(args.output_dir).expanduser().resolve() / f"{args.channel}-{run_id}"
    scratch = out_dir / "build"
    scratch.mkdir(parents=True, exist_ok=True)

    # 1. build items measurement-only (no DB writes)
    items, marc_records = await channel.build_measurement(run_id, scratch)
    items_by_id = {_local_id(i): i for i in items if _local_id(i)}

    # 2. baseline verdicts (export file preferred, else DB read-only)
    if args.baseline_export:
        baseline = _baseline_from_export(Path(args.baseline_export).expanduser())
        print(f"[scope] baseline from export: {len(baseline)} verdicts")
    else:
        baseline = await channel.baseline_from_db(run_id)
        print(f"[scope] baseline from DB overrides: {len(baseline)} verdicts")

    # 3. scope selection
    if args.local_id:
        scope_ids = [lid for lid in args.local_id if lid in items_by_id]
    elif args.scope == "all":
        scope_ids = list(items_by_id)
    else:  # non-passing
        scope_ids = [
            lid for lid in items_by_id
            if str((baseline.get(lid) or {}).get("overall") or "") in BAD_OVERALLS
        ]
    scope_ids.sort()
    if args.limit is not None:
        scope_ids = scope_ids[: args.limit]
    if not scope_ids:
        print("[scope] no items in scope — nothing to verify.")
        return 0
    print(f"[scope] {len(scope_ids)} item(s) to re-verify with {args.tier_model}")

    # 4. write fixture (scoped items + marc) and run eval-agent
    scoped_items = []
    for lid in scope_ids:
        it = dict(items_by_id[lid])
        it["_local_id"] = lid
        scoped_items.append(it)
    attach_marc_context(scoped_items, marc_records)
    pipeline_dir = out_dir / "pipeline-output"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    (pipeline_dir / "marc_extracted.json").write_text(
        json.dumps(marc_records, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (pipeline_dir / channel.items_filename).write_text(
        json.dumps(scoped_items, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    verdicts = _run_eval_agent(
        pipeline_dir=pipeline_dir,
        state_dir=out_dir / "eval-state",
        evaluator=channel.evaluator,
        tier_model=args.tier_model,
        rpm=args.rpm,
    )

    # 5. before/after report
    transitions: Counter[str] = Counter()
    new_dist: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    still_bad = 0
    for lid in scope_ids:
        before = str((baseline.get(lid) or {}).get("overall") or "none")
        vd = (verdicts.get(lid) or {}).get("verdict") or {}
        after = str(vd.get("overall") or "missing")
        new_dist[after] += 1
        transitions[f"{before}->{after}"] += 1
        if after in BAD_OVERALLS or after == "missing":
            still_bad += 1
        rows.append({
            "local_id": lid,
            "entity_type": items_by_id[lid].get("entity_type"),
            "before": before,
            "after": after,
            "name_ok": vd.get("name_ok"),
            "type_ok": vd.get("type_ok"),
            "role_ok": vd.get("role_ok"),
            "reasoning": (vd.get("reasoning") or "")[:300],
        })

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "channel": args.channel,
        "run_id": str(run_id),
        "tier_model": args.tier_model,
        "scope": args.scope if not args.local_id else "local-id",
        "scope_size": len(scope_ids),
        "new_verdict_distribution": dict(new_dist),
        "transitions": dict(transitions),
        "now_passing": len(scope_ids) - still_bad,
        "still_partial_or_fail": still_bad,
        "rows": rows,
    }
    report_path = out_dir / f"measure_report_{stamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== MEASUREMENT REPORT =====")
    print(f"scope: {len(scope_ids)}  new_dist: {dict(new_dist)}")
    print(f"now passing (full/partial-acceptable→full): {report['now_passing']}")
    print(f"still partial/fail/missing: {still_bad}")
    print("transitions:")
    for k, v in sorted(transitions.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    print(f"\nwrote {report_path}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--channel", choices=sorted(CHANNELS), default="hmo")
    p.add_argument("--run-id", default=DEFAULT_RUN_ID)
    p.add_argument("--scope", choices=("non-passing", "all"), default="non-passing")
    p.add_argument("--baseline-export", default=None,
                   help="export JSON of the prior verify (for scope + before/after)")
    p.add_argument("--local-id", action="append", default=None, help="repeatable")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--tier-model", default=DEFAULT_TIER_MODEL)
    p.add_argument("--rpm", type=int, default=30)
    p.add_argument("--output-dir", default=str(_REPO / "state" / "local-measure-verify"))
    args = p.parse_args()
    raise SystemExit(asyncio.run(_async_main(args)))


if __name__ == "__main__":
    main()
