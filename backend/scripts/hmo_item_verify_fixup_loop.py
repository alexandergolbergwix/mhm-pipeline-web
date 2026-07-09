#!/usr/bin/env python3
"""HMO Wikibase item AI-verify fix loop for one run.

Fetches items whose stored AI verdict is ``partial`` / ``fail`` (or any
``name_ok`` / ``type_ok`` / ``role_ok`` of ``partial`` / ``no``), runs the
eval-agent ``hmo_wikibase_item`` judge on each entity (one at a time via
Qubrid Kimi K2.5 by default), attempts heuristic fixes, optionally rebuilds
RDF + items, and re-judges until the verdict looks acceptable or iterations
are exhausted.

Environment
-----------
- ``DATABASE_URL`` — Postgres (Heroku production or local).
- ``QUBRID_API_KEY`` — Qubrid OpenAI-compatible key (alias ``QUABRID_API_KEY``
  accepted). Falls back to ``heroku config:get QUBRID_API_KEY -a mhm-pipeline-web``
  when unset.
- Loads ``.env`` from the repo root when present.

Examples
--------
Pilot on five bad items (eval only, no rebuild)::

    cd backend
    python -m scripts.hmo_item_verify_fixup_loop \\
        --run-id 48ba6c13-115c-4763-bff1-c08b9031b518 --limit 5

Full fix loop with rebuild between attempts::

    python -m scripts.hmo_item_verify_fixup_loop \\
        --run-id 48ba6c13-115c-4763-bff1-c08b9031b518 --limit 5 \\
        --loop --max-iterations 3 --rebuild

Single entity::

    python -m scripts.hmo_item_verify_fixup_loop \\
        --run-id 48ba6c13-115c-4763-bff1-c08b9031b518 \\
        --local-id QDraft_Person_52 --loop --rebuild
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

_BACKEND = Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.db import session_scope  # noqa: E402
from app.models.hmo_studio_item_override import HmoStudioItemOverride  # noqa: E402
from app.pipeline.agent_runner import _python_for, locate_eval_agent  # noqa: E402
from app.pipeline.hmo_item_views import fetch_merged_hmo_items, item_label  # noqa: E402
from app.pipeline.marc_verify_context import (  # noqa: E402
    attach_marc_context,
    load_run_marc_records,
)

logger = logging.getLogger(__name__)

DEFAULT_RUN_ID = "48ba6c13-115c-4763-bff1-c08b9031b518"
DEFAULT_TIER_MODEL = "moonshotai/Kimi-K2.5"
PRIORITY_ENTITY_TYPES = (
    "E21_Person",
    "F1_Work",
    "F2_Expression",
    "E53_Place",
    "Manuscript",
    "Codicological_Unit",
)
SKIP_ENTITY_TYPES = frozenset({
    "ViewType",
    "BibliographicParadigm",
    "PhilologicalParadigm",
    "AnthologyPosition",
    "CatalogStep",
    "EvidenceStep",
    "EvidenceChain",
    "Evidence",
    "PhilologicalView",
})
GENERIC_HMO_RE = re.compile(
    r"in the Hebrew Manuscripts Ontology \(HMO\)",
    re.IGNORECASE,
)
IN_MS_RE = re.compile(r"\(in MS\b", re.IGNORECASE)


@dataclass
class Issue:
    code: str
    message: str
    fixable: str = "manual"  # override | rebuild | manual


@dataclass
class EntityReport:
    local_id: str
    entity_type: str
    label: str
    iterations: list[dict[str, Any]] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    final_overall: str | None = None
    acceptable: bool = False


def _load_dotenv() -> None:
    env_path = _REPO / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def _resolve_qubrid_key() -> str:
    for name in ("QUBRID_API_KEY", "QUABRID_API_KEY"):
        val = os.environ.get(name, "").strip()
        if val:
            return val
    try:
        out = subprocess.run(
            ["heroku", "config:get", "QUBRID_API_KEY", "-a", "mhm-pipeline-web"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        val = (out.stdout or "").strip()
        if val and not val.startswith("Error"):
            os.environ["QUBRID_API_KEY"] = val
            return val
    except (OSError, subprocess.TimeoutExpired):
        pass
    raise RuntimeError(
        "Set QUBRID_API_KEY (or QUABRID_API_KEY) or configure Heroku CLI access.",
    )


def _is_bad_verdict(av: dict[str, Any] | None) -> bool:
    if not av:
        return False
    if av.get("overall") in ("partial", "fail"):
        return True
    return any(av.get(k) in ("partial", "no") for k in ("name_ok", "type_ok", "role_ok"))


def _is_acceptable_verdict(vd: dict[str, Any]) -> bool:
    overall = str(vd.get("overall") or "")
    if overall == "full":
        return True
    if overall == "partial":
        name_ok = vd.get("name_ok")
        type_ok = vd.get("type_ok")
        role_ok = vd.get("role_ok")
        if name_ok in (None, "yes", "n/a") and type_ok in (None, "yes", "n/a"):
            if role_ok in (None, "yes", "n/a"):
                return True
    return False


def _diagnose(verdict: dict[str, Any], item: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    reasoning = str(verdict.get("reasoning") or "")
    labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
    descriptions = (
        item.get("descriptions") if isinstance(item.get("descriptions"), dict) else {}
    )
    shacl = item.get("shacl_issues") or []
    marc_ctx = item.get("_marc_context") if isinstance(item.get("_marc_context"), dict) else {}

    if item.get("has_blocking_shacl") or any(
        isinstance(s, dict) and s.get("severity") in ("Violation", "Error")
        for s in shacl
    ):
        issues.append(Issue(
            code="blocking_shacl",
            message="Blocking SHACL Violation/Error on built item",
            fixable="rebuild",
        ))

    if not marc_ctx and item.get("control_numbers"):
        issues.append(Issue(
            code="marc_out_of_run",
            message=(
                "control_numbers present but no MARC slice in this run — "
                "linked manuscripts may be outside run scope"
            ),
            fixable="manual",
        ))

    for lang, text in labels.items():
        text_s = str(text or "")
        if lang == "und" or text_s.startswith("und|"):
            issues.append(Issue(
                code="und_label",
                message=f"Unsupported language code in label ({lang!r})",
                fixable="rebuild",
            ))
        if IN_MS_RE.search(text_s):
            issues.append(Issue(
                code="in_ms_suffix_label",
                message="Label contains '(in MS …)' scope suffix",
                fixable="rebuild",
            ))

    for text in descriptions.values():
        if GENERIC_HMO_RE.search(str(text or "")):
            issues.append(Issue(
                code="generic_hmo_description",
                message="Generic HMO fallback description",
                fixable="rebuild",
            ))

    if verdict.get("name_ok") in ("partial", "no"):
        if "label" in reasoning.lower() or "mismatch" in reasoning.lower():
            issues.append(Issue(
                code="label_quality",
                message=reasoning[:240],
                fixable="rebuild",
            ))
        elif labels.get("en") and labels.get("he") and labels.get("en") == labels.get("he"):
            issues.append(Issue(
                code="label_lang_swap",
                message="English and Hebrew labels are identical",
                fixable="override",
            ))

    if verdict.get("type_ok") in ("partial", "no"):
        issues.append(Issue(
            code="type_mismatch",
            message=reasoning[:240] or "type_ok not yes",
            fixable="rebuild",
        ))

    if verdict.get("role_ok") in ("partial", "no"):
        issues.append(Issue(
            code="role_or_claims",
            message=reasoning[:240] or "role_ok not yes/n/a",
            fixable="rebuild",
        ))

    if not issues and not _is_acceptable_verdict(verdict):
        issues.append(Issue(
            code="other",
            message=reasoning[:240] or "verdict not acceptable",
            fixable="manual",
        ))
    return issues


def _run_eval_agent(
    *,
    pipeline_dir: Path,
    state_dir: Path,
    tier_model: str,
    api_key: str,
) -> dict[str, Any]:
    root = locate_eval_agent()
    py = _python_for(root)
    cmd = [
        py,
        "-m",
        "eval_agent.cli",
        "run",
        "--pipeline-output",
        str(pipeline_dir),
        "--evaluators",
        "hmo_wikibase_item",
        "--linear",
        "--no-cache",
        "--no-self-verify",
        "--threshold",
        "-1",
        "--tier-model",
        tier_model,
        "--rpm",
        "30",
        "--state-dir",
        str(state_dir),
    ]
    env = os.environ.copy()
    env["QUBRID_API_KEY"] = api_key
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.run(
        cmd,
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-1500:]
        raise RuntimeError(f"eval-agent exited {proc.returncode}: {tail}")

    runs_dir = state_dir / "runs"
    if not runs_dir.is_dir():
        raise RuntimeError("eval-agent produced no runs/ directory")
    run_dirs = sorted(p for p in runs_dir.iterdir() if p.is_dir())
    if not run_dirs:
        raise RuntimeError("eval-agent produced no run artefacts")
    results_path = run_dirs[-1] / "results.jsonl"
    if not results_path.is_file():
        raise RuntimeError(f"missing {results_path}")
    line = results_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    row = json.loads(line)
    verdict = row.get("verdict") if isinstance(row.get("verdict"), dict) else {}
    return {
        "verdict": verdict,
        "judge_id": row.get("judge_id"),
        "judged_at": row.get("judged_at"),
        "raw": row,
    }


def _write_fixture(
    pipeline_dir: Path,
    item: dict[str, Any],
    marc_records: list[dict[str, Any]],
) -> None:
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    eval_item = dict(item)
    eval_item["_local_id"] = item.get("local_id") or item.get("_local_id")
    eval_item.setdefault("label", item_label(item))
    (pipeline_dir / "marc_extracted.json").write_text(
        json.dumps(marc_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (pipeline_dir / "hmo_wikibase_items.json").write_text(
        json.dumps([eval_item], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def _fetch_scope(
    run_id: uuid.UUID,
    *,
    local_ids: list[str] | None,
    limit: int | None,
    include_no_verdict: bool,
    skip_structural: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    async with session_scope() as db:
        items = await fetch_merged_hmo_items(db, run_id)
        marc_records = await load_run_marc_records(db, run_id)
        override_rows = (
            await db.execute(
                select(HmoStudioItemOverride).where(
                    HmoStudioItemOverride.run_id == run_id,
                )
            )
        ).scalars().all()
        stored_verdicts = {
            r.local_id: (r.ai_verdict or {})
            for r in override_rows
            if r.ai_verdict
        }

    by_id = {str(i.get("local_id") or ""): i for i in items}
    if local_ids:
        scoped = [by_id[lid] for lid in local_ids if lid in by_id]
    else:
        scoped = []
        for i in items:
            lid = str(i.get("local_id") or "")
            av = stored_verdicts.get(lid) or i.get("ai_verdict") or {}
            if include_no_verdict and not av:
                scoped.append(i)
            elif _is_bad_verdict(av):
                scoped.append(i)
        if skip_structural:
            scoped = [
                i for i in scoped
                if str(i.get("entity_type") or "") not in SKIP_ENTITY_TYPES
                and not str(i.get("local_id") or "").startswith("QDraft_BlankNode")
            ]
        scoped.sort(key=lambda row: (
            PRIORITY_ENTITY_TYPES.index(row["entity_type"])
            if row.get("entity_type") in PRIORITY_ENTITY_TYPES
            else 99,
            str(row.get("local_id") or ""),
        ))
        if limit is not None:
            scoped = scoped[:limit]

    attach_marc_context(scoped, marc_records)
    return scoped, marc_records, stored_verdicts


async def _apply_override_fix(
    db: Any,
    run_id: uuid.UUID,
    local_id: str,
    *,
    labels: dict[str, str | None] | None = None,
    descriptions: dict[str, str | None] | None = None,
) -> bool:
    row = (
        await db.execute(
            select(HmoStudioItemOverride).where(
                HmoStudioItemOverride.run_id == run_id,
                HmoStudioItemOverride.local_id == local_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    changed = False
    if labels:
        cur = dict(row.labels or {})
        for k, v in labels.items():
            if v is None:
                cur.pop(k, None)
            else:
                cur[k] = v
        row.labels = cur
        changed = True
    if descriptions:
        cur = dict(row.descriptions or {})
        for k, v in descriptions.items():
            if v is None:
                cur.pop(k, None)
            else:
                cur[k] = v
        row.descriptions = cur
        changed = True
    if changed:
        await db.commit()
    return changed


def _heuristic_fix_from_marc(item: dict[str, Any], issues: list[Issue]) -> dict[str, Any]:
    """Return optional override patch derived from MARC + issue codes."""
    patch: dict[str, Any] = {}
    marc = item.get("_marc_context") if isinstance(item.get("_marc_context"), dict) else {}
    entity_type = str(item.get("entity_type") or "")
    codes = {i.code for i in issues}

    if "label_lang_swap" in codes and marc:
        lat = ""
        for key in ("authors", "contributors", "subjects", "title"):
            chunk = marc.get(key) or ""
            if chunk and not re.search(r"[\u0590-\u05FF]", chunk):
                lat = chunk.split("|")[0].strip()
                break
        if lat:
            patch["labels"] = {"en": lat}

    if "generic_hmo_description" in codes and marc:
        bits: list[str] = []
        for key in ("title", "authors", "notes", "contents", "colophon_text"):
            val = marc.get(key)
            if val:
                bits.append(str(val)[:200])
        if bits:
            desc = " · ".join(bits)[:500]
            patch["descriptions"] = {"en": desc, "he": desc}

    if "label_quality" in codes and entity_type in {"F2_Expression", "F1_Work"} and marc:
        title = marc.get("title") or marc.get("contents")
        if title:
            clean = re.sub(r"^\d+\)\s*", "", str(title).split("|")[0].strip())
            clean = IN_MS_RE.sub("", clean).strip()
            if clean:
                patch.setdefault("labels", {})["he"] = clean
                if "en" not in (patch.get("labels") or {}):
                    patch["labels"]["en"] = clean

    return patch


async def _rebuild_run(run_id: uuid.UUID) -> None:
    from scripts.rebuild_run_rdf_and_items import rebuild  # noqa: PLC0415

    await rebuild(run_id)


async def _process_entity(
    *,
    run_id: uuid.UUID,
    item: dict[str, Any],
    marc_records: list[dict[str, Any]],
    work_dir: Path,
    state_dir: Path,
    tier_model: str,
    api_key: str,
    loop: bool,
    max_iterations: int,
    rebuild: bool,
) -> EntityReport:
    local_id = str(item.get("local_id") or "")
    report = EntityReport(
        local_id=local_id,
        entity_type=str(item.get("entity_type") or ""),
        label=item_label(item),
    )

    for iteration in range(1, max_iterations + 1):
        pipeline_dir = work_dir / local_id / f"iter_{iteration}"
        _write_fixture(pipeline_dir, item, marc_records)
        eval_out = _run_eval_agent(
            pipeline_dir=pipeline_dir,
            state_dir=state_dir / local_id,
            tier_model=tier_model,
            api_key=api_key,
        )
        vd = eval_out["verdict"]
        issues = _diagnose(vd, item)
        acceptable = _is_acceptable_verdict(vd)
        report.iterations.append({
            "iteration": iteration,
            "verdict": vd,
            "acceptable": acceptable,
            "issues": [asdict(i) for i in issues],
        })
        report.issues = issues
        report.final_overall = str(vd.get("overall") or "")
        report.acceptable = acceptable

        print(
            f"  [{local_id}] iter {iteration}: overall={vd.get('overall')} "
            f"name_ok={vd.get('name_ok')} type_ok={vd.get('type_ok')} "
            f"role_ok={vd.get('role_ok')}",
        )
        print(f"    reasoning: {(vd.get('reasoning') or '')[:200]}")

        if acceptable or not loop:
            break

        patch = _heuristic_fix_from_marc(item, issues)
        applied = False
        if patch:
            async with session_scope() as db:
                applied = await _apply_override_fix(
                    db,
                    run_id,
                    local_id,
                    labels=patch.get("labels"),
                    descriptions=patch.get("descriptions"),
                )
            if applied:
                from app.pipeline.hmo_item_merge import apply_hmo_item_override  # noqa: PLC0415

                item = apply_hmo_item_override(item, patch)
                attach_marc_context([item], marc_records)
                print(f"    applied override patch: {json.dumps(patch, ensure_ascii=False)}")

        needs_rebuild = rebuild or any(
            i.fixable == "rebuild" for i in issues
        )
        if needs_rebuild and iteration < max_iterations:
            print("    rebuilding RDF + HMO items …")
            await _rebuild_run(run_id)
            async with session_scope() as db:
                fresh_items = await fetch_merged_hmo_items(db, run_id)
                marc_records = await load_run_marc_records(db, run_id)
            item = next(i for i in fresh_items if i.get("local_id") == local_id)
            attach_marc_context([item], marc_records)
            continue

        if not applied and not needs_rebuild:
            print("    no automatic fix applied — needs manual/code change")
            break

    return report


async def _async_main(args: argparse.Namespace) -> int:
    _load_dotenv()
    api_key = _resolve_qubrid_key()
    if not os.environ.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is required (Heroku or .env).")

    run_id = uuid.UUID(args.run_id)
    local_ids = [args.local_id] if args.local_id else None
    scoped, marc_records, stored = await _fetch_scope(
        run_id,
        local_ids=local_ids,
        limit=args.limit,
        include_no_verdict=args.include_no_verdict,
        skip_structural=args.skip_structural,
    )

    if not scoped:
        print("No items in scope.")
        return 0

    print(
        f"Scope: {len(scoped)} item(s) on run {run_id} "
        f"(tier_model={args.tier_model})",
    )
    for item in scoped:
        lid = item.get("local_id")
        av = stored.get(str(lid), {})
        print(
            f"  - {lid} ({item.get('entity_type')}) "
            f"stored_overall={av.get('overall')}",
        )

    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = out_dir / "fixtures"
    state_dir = out_dir / "eval-state"
    reports: list[dict[str, Any]] = []

    for item in scoped:
        print(f"\n=== {item.get('local_id')} ===")
        entity_report = await _process_entity(
            run_id=run_id,
            item=item,
            marc_records=marc_records,
            work_dir=work_dir,
            state_dir=state_dir,
            tier_model=args.tier_model,
            api_key=api_key,
            loop=args.loop,
            max_iterations=args.max_iterations,
            rebuild=args.rebuild,
        )
        reports.append(asdict(entity_report))

    summary_path = out_dir / f"fixup_summary_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    summary_path.write_text(
        json.dumps({
            "run_id": str(run_id),
            "tier_model": args.tier_model,
            "entity_count": len(reports),
            "acceptable_count": sum(1 for r in reports if r.get("acceptable")),
            "reports": reports,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nWrote {summary_path}")
    print(
        f"Acceptable: {sum(1 for r in reports if r.get('acceptable'))}/{len(reports)}",
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--limit", type=int, default=5, help="max entities (use --all for full run)")
    parser.add_argument(
        "--all",
        action="store_true",
        help="process every bad item (overrides --limit)",
    )
    parser.add_argument("--local-id", default=None)
    parser.add_argument("--tier-model", default=DEFAULT_TIER_MODEL)
    parser.add_argument("--output-dir", default=str(_REPO / "state" / "hmo-item-fixup"))
    parser.add_argument("--loop", action="store_true", help="retry with fixes until acceptable")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="rebuild RDF + items between iterations when fixes need it",
    )
    parser.add_argument(
        "--include-no-verdict",
        action="store_true",
        help="also include items with no stored ai_verdict",
    )
    parser.add_argument(
        "--include-structural",
        action="store_true",
        help="include structural/blank-node items in scope",
    )
    args = parser.parse_args()
    args.skip_structural = not args.include_structural
    if args.all:
        args.limit = None
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(_async_main(args)))


if __name__ == "__main__":
    main()
