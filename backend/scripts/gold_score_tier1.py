#!/usr/bin/env python3
"""Score the eval-agent tier-1 judge (Qubrid) against the Jev gold set.

Runs the tier-1 judge over a stratified subset of the certification gold,
then scores overall/axes/unsafe-approve exactly like the Jev gold mode.
Read-only; writes JSON scorecards next to the gold files.

    cd backend && DATABASE_URL=... QUBRID_API_KEY=... .venv/bin/python \
        -m scripts.gold_score_tier1 --combo hmo:60 --combo wikidata:60 \
        --combo person_ner:40 --combo contents_ner:30 --combo provenance_ner:20 \
        --combo genre_classifier:30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.pipeline.agent_runner import locate_eval_agent  # noqa: E402

if str(locate_eval_agent()) not in sys.path:
    sys.path.insert(0, str(locate_eval_agent()))

from eval_agent.evaluators import build as build_evaluator  # noqa: E402

from scripts.typesafe_bakeoff import (  # noqa: E402
    NAXIS,
    RAXIS,
    TAXIS,
    _baseline_verdict,
    _candidate_key,
    _load_fixture_records,
    _norm_axis,
    _pairing_and_candidates,
    _write_fixture,
    _build_hmo,
    _build_ner,
    _build_wikidata,
    _load_dotenv,
    _run_tier1_baseline,
)
from scripts.local_measure_verify import _resolve_tier_key  # noqa: E402

RUN_ID = uuid.UUID("48ba6c13-115c-4763-bff1-c08b9031b518")
GOLD_DIR = _REPO / "state" / "typesafe-bakeoff" / "gold-v1"
OUT_DIR = _REPO / "state" / "typesafe-bakeoff" / "jev-vs-qubrid"

COMBOS = {
    "hmo": ("hmo", "hmo_wikibase_item", "gold_hmo.jsonl", 2500),
    "wikidata": ("wikidata", "wikidata_item", "gold_wikidata.jsonl", 500),
    "person_ner": ("ner", "person_ner", "gold_ner_person.jsonl", 1500),
    "contents_ner": ("ner", "contents_ner", "gold_ner_contents.jsonl", 1500),
    "provenance_ner": ("ner", "provenance_ner", "gold_ner_provenance.jsonl", 1500),
    "genre_classifier": ("ner", "genre_classifier", "gold_ner_genre.jsonl", 1500),
}
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _stratified_subset(
    candidates: list[tuple[Any, Any]],
    gold: dict[str, dict[str, Any]],
    evaluator_id: str,
    n: int,
) -> list[tuple[Any, Any]]:
    by_class: dict[str, list[tuple[Any, Any]]] = {"full": [], "partial": [], "fail": []}
    for rec, cand in candidates:
        key = _candidate_key(evaluator_id, dict(cand.payload))
        g = gold.get(key)
        if not g:
            continue
        o = str((g.get("label") or {}).get("overall") or "")
        if o in by_class:
            by_class[o].append((rec, cand))
    per = max(1, n // 3)
    picked: list[tuple[Any, Any]] = []
    leftovers: list[tuple[Any, Any]] = []
    for rows in by_class.values():
        picked.extend(rows[:per])
        leftovers.extend(rows[per:])
    for row in leftovers:
        if len(picked) >= n:
            break
        picked.append(row)
    return picked


def _score(judged: list[dict[str, Any]], gold: dict[str, dict[str, Any]]) -> dict[str, Any]:
    matrix: Counter[str] = Counter()
    unsafe: list[str] = []
    safe_miss: list[str] = []
    agree = 0
    axis_pairs: dict[str, list[tuple[str, str]]] = {
        a: [] for a in (NAXIS, TAXIS, RAXIS)
    }
    for r in judged:
        g = (gold[r["key"]].get("label") or {})
        go = str(g.get("overall"))
        v = r["verdict"]
        jo = str(v.get("overall"))
        matrix[f"{go}->{jo}"] += 1
        if go == jo:
            agree += 1
        if jo == "full" and go in ("partial", "fail"):
            unsafe.append(r["key"])
        elif jo == "fail" and go == "full":
            safe_miss.append(r["key"])
        gaxes = g.get("axes") or {}
        for axis in (NAXIS, TAXIS, RAXIS):
            gv = _norm_axis(gaxes.get(axis))
            jv = _norm_axis(v.get(axis))
            if gv in ("yes", "partial", "no") and jv != "unknown":
                axis_pairs[axis].append((gv, jv))
    n = len(judged)
    axis_reports = {}
    for axis, pairs in axis_pairs.items():
        if pairs:
            ok = sum(1 for b, j in pairs if b == j)
            axis_reports[axis] = {
                "n": len(pairs), "agreement": round(ok / len(pairs), 3),
            }
    return {
        "scored": n,
        "accuracy": round(agree / n, 3) if n else None,
        "unsafe_approve": {"count": len(unsafe), "keys": unsafe[:20]},
        "safe_miss": {"count": len(safe_miss), "keys": safe_miss[:20]},
        "confusion": dict(matrix),
        "axis_vs_gold": axis_reports,
    }


async def _one_combo(
    name: str, tier_model: str, n: int, rpm: int,
) -> dict[str, Any]:
    channel, evaluator_id, gold_name, frame_limit = COMBOS[name]
    env_name, tier_key = _resolve_tier_key(tier_model)
    if not tier_key:
        raise SystemExit(f"missing {env_name} for {tier_model}")
    gold = {
        json.loads(l)["key"]: json.loads(l)
        for l in (GOLD_DIR / gold_name).open()
        if l.strip()
    }
    evaluator = build_evaluator(evaluator_id)
    out_dir = OUT_DIR / f"tier1-{name}"
    pipeline_dir = out_dir / "pipeline-output"
    if (pipeline_dir / "marc_extracted.json").is_file():
        from app.pipeline.marc_verify_context import index_marc_records

        marc_records = list(
            index_marc_records(
                json.loads(
                    (pipeline_dir / "marc_extracted.json").read_text(
                        encoding="utf-8",
                    ),
                ),
            ).values(),
        )
    else:
        if channel == "ner":
            items, marc_records = await _build_ner(
                RUN_ID, evaluator_id, max_predictions=frame_limit,
            )
        elif channel == "hmo":
            items, marc_records = await _build_hmo(RUN_ID, out_dir / "build")
            items = [
                i for i in items
                if not str(i.get("local_id") or "").startswith("QDraft_BlankNode")
            ]
        else:
            items, marc_records = await _build_wikidata(RUN_ID)
        _write_fixture(pipeline_dir, channel, items[:frame_limit], marc_records)
    fixture_records = _load_fixture_records(channel, pipeline_dir)
    candidates = _pairing_and_candidates(evaluator, fixture_records, marc_records)
    subset = _stratified_subset(candidates, gold, evaluator_id, n)
    keys = [
        _candidate_key(evaluator_id, dict(cand.payload)) for _rec, cand in subset
    ]
    print(f"[{name}] judging {len(subset)} gold row(s) with {tier_model}")
    t0 = time.monotonic()
    rows_map, _ = _run_tier1_baseline(
        pipeline_dir=pipeline_dir,
        state_dir=out_dir / "eval-state",
        evaluator=evaluator_id,
        tier_model=tier_model,
        rpm=rpm,
    )
    wall = time.monotonic() - t0
    judged = []
    for k in keys:
        row = rows_map.get(k)
        if not row:
            continue
        v = _baseline_verdict(row)
        if str(v.get("overall") or "") in ("full", "pass", "partial", "fail"):
            judged.append({"key": k, "verdict": v})
    manifest_tokens = {}
    runs_dir = out_dir / "eval-state" / "runs"
    run_dirs = sorted(runs_dir.glob("*")) if runs_dir.is_dir() else []
    if run_dirs:
        manifest = run_dirs[-1] / "manifest.json"
        if manifest.is_file():
            manifest_tokens = json.load(open(manifest)).get("stats") or {}
    score = _score(judged, gold)
    result = {
        "combo": name,
        "channel": channel,
        "evaluator": evaluator_id,
        "tier_model": tier_model,
        "subset": len(subset),
        "judged": len(judged),
        "wall_s": round(wall, 1),
        "tokens": {
            k: manifest_tokens.get(k) for k in
            ("candidates_total", "input_tokens", "output_tokens")
        },
        "gold": score,
    }
    out = OUT_DIR / f"tier1_score_{name}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[{name}] accuracy={score['accuracy']} "
          f"unsafe={score['unsafe_approve']['count']} "
          f"safe_miss={score['safe_miss']['count']} wall={result['wall_s']}s")
    return result


async def main_async(args: argparse.Namespace) -> int:
    _load_dotenv()
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is required")
    for combo in args.combo:
        name, _, n_s = combo.partition(":")
        if name not in COMBOS:
            raise SystemExit(f"unknown combo {name}; known: {sorted(COMBOS)}")
        await _one_combo(name, args.tier_model, int(n_s or 50), args.rpm)
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--combo", action="append", default=[],
                   help="name[:n], e.g. hmo:60 (repeatable)")
    p.add_argument("--tier-model", default="moonshotai/Kimi-K2.5")
    p.add_argument("--rpm", type=int, default=30)
    args = p.parse_args()
    if not args.combo:
        args.combo = [f"{k}:{v}" for k, v in
                      (("hmo", 60), ("wikidata", 60), ("person_ner", 40),
                       ("contents_ner", 30), ("provenance_ner", 20),
                       ("genre_classifier", 30))]
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
