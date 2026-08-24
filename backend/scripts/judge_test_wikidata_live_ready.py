#!/usr/bin/env python3
"""LLM live-readiness judge for items already written to test.wikidata.org.

Hard gate = ``audit_test_wikidata_upload.audit_one`` (live URI leak, identity
clash, missing claims, validator ERROR, unresolved ``__LOCAL:`` on written
rows). LLM gate = eval-agent ``wikidata_test_live_ready`` judging the Studio
*native* (live P/Q) with the test.wikidata.org snapshot as landing evidence
only. Test Q/P ids are never a live write payload (Rules W-182 / W-183).

Read-only against Postgres, test.wikidata.org, and www.wikidata.org.

    cd backend
    DATABASE_URL=$(heroku config:get DATABASE_URL -a mhm-pipeline-web) \\
      .venv/bin/python -m scripts.judge_test_wikidata_live_ready \\
      --run-id 48ba6c13-115c-4763-bff1-c08b9031b518

Pilot five written items::

    ... --limit 5 --json-out /tmp/test-live-ready.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import requests

_BACKEND = Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scripts.audit_test_wikidata_upload import (  # noqa: E402
    WRITTEN,
    _LIVE_API,
    _QID_BATCH,
    _TEST_API,
    _USER_AGENT,
    _connect,
    audit_one,
    count_wikibase_claims,
    entity_en_description,
    entity_labels,
    fetch_entities,
    load_latest_test_job,
    load_studio_items,
)

EVALUATOR_ID = "wikidata_test_live_ready"
DEFAULT_TIER_MODEL = "deepseek-ai/DeepSeek-V4-Flash"
LLM_FULL = frozenset({"full", "pass"})
_CLAIM_LIMIT = 40


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


def summarise_datavalue(value: Any, labels: dict[str, str]) -> str:
    if isinstance(value, dict):
        qid = str(value.get("id") or "")
        if qid.startswith(("Q", "P")):
            lab = labels.get(qid) or ""
            return f"{qid} ({lab})" if lab else qid
        if "amount" in value:
            amount = str(value.get("amount") or "")
            unit = str(value.get("unit") or "")
            if "entity/" in unit:
                unit = unit.rsplit("/", 1)[-1]
            unit_lab = labels.get(unit) or unit
            return f"{amount} {unit_lab}".strip()
        if "time" in value:
            return str(value.get("time") or "")
        if "text" in value:
            lang = str(value.get("language") or "")
            text = str(value.get("text") or "")
            return f"{text}@{lang}" if lang else text
    if value is None:
        return ""
    return str(value)[:200]


def compact_wikibase_entity(
    entity: dict[str, Any] | None,
    labels: dict[str, str] | None = None,
    *,
    wiki: str,
    limit: int = _CLAIM_LIMIT,
) -> dict[str, Any]:
    labels = labels or {}
    if not isinstance(entity, dict) or entity.get("missing") is not None:
        return {
            "wiki": wiki,
            "missing": True,
            "qid": None,
            "claims": [],
            "claim_count": 0,
            "ref_ids": [],
        }
    qid = str(entity.get("id") or "")
    host = "test.wikidata.org" if wiki == "test" else "www.wikidata.org"
    claims_out: list[dict[str, Any]] = []
    ref_ids: list[str] = []
    claims = entity.get("claims") or {}
    if isinstance(claims, dict):
        for pid, clist in claims.items():
            pid_s = str(pid)
            ref_ids.append(pid_s)
            if not isinstance(clist, list):
                continue
            for claim in clist:
                snak = (claim or {}).get("mainsnak") or {}
                dv = (snak.get("datavalue") or {}).get("value")
                if isinstance(dv, dict) and str(dv.get("id") or "").startswith(("Q", "P")):
                    ref_ids.append(str(dv["id"]))
                claims_out.append({
                    "property": pid_s,
                    "property_label": labels.get(pid_s) or "",
                    "datatype": snak.get("datatype") or "",
                    "value": summarise_datavalue(dv, labels),
                })
                if len(claims_out) >= limit:
                    break
            if len(claims_out) >= limit:
                break
    return {
        "wiki": wiki,
        "qid": qid or None,
        "url": f"https://{host}/wiki/{qid}" if qid else None,
        "labels": entity_labels(entity),
        "description_en": entity_en_description(entity),
        "claim_count": count_wikibase_claims(entity),
        "claims": claims_out,
        "ref_ids": ref_ids,
    }


def compact_studio_item(studio: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(studio, dict):
        return {}
    statements: list[dict[str, Any]] = []
    for stmt in studio.get("statements") or []:
        if not isinstance(stmt, dict):
            continue
        statements.append({
            "property": stmt.get("property_id") or stmt.get("property"),
            "property_label": stmt.get("property_label"),
            "value": stmt.get("value") or stmt.get("value_id"),
            "value_label": stmt.get("value_label"),
            "value_type": stmt.get("value_type"),
            "unit": stmt.get("unit") or None,
        })
        if len(statements) >= _CLAIM_LIMIT:
            break
    return {
        "local_id": studio.get("local_id"),
        "entity_type": studio.get("entity_type"),
        "labels": studio.get("labels") or {},
        "descriptions": studio.get("descriptions") or {},
        "existing_qid": studio.get("existing_qid"),
        "statement_count": len(studio.get("statements") or []),
        "statements": statements,
        "record_ids": studio.get("records") or studio.get("record_ids") or [],
        "validation_issues": studio.get("validation_issues") or [],
    }


def merge_live_ready(
    *,
    audit: dict[str, Any],
    llm: dict[str, Any] | None,
    judged: bool,
) -> dict[str, Any]:
    blockers = list(audit.get("blockers") or [])
    written = str(audit.get("status") or "") in WRITTEN
    overall = str((llm or {}).get("overall") or "")
    if audit.get("skip_for_live"):
        live_ready = False
        gate = "skipped_for_live"
    elif blockers:
        live_ready = False
        gate = "deterministic_blockers"
    elif not written:
        live_ready = False
        gate = "not_written"
    elif not judged:
        live_ready = bool(audit.get("ready_for_live"))
        gate = "deterministic_only"
    elif llm is None:
        live_ready = False
        gate = "llm_missing"
    elif overall == "verification_failed":
        live_ready = False
        gate = "llm_judge_failure"
    elif overall in LLM_FULL:
        live_ready = True
        gate = "llm_full"
    else:
        live_ready = False
        gate = f"llm_{overall or 'fail'}"
    return {
        **audit,
        "llm": None if llm is None else {
            "overall": llm.get("overall"),
            "name_ok": llm.get("name_ok"),
            "type_ok": llm.get("type_ok"),
            "role_ok": llm.get("role_ok"),
            "reasoning": str(llm.get("reasoning") or "")[:800],
        },
        "live_ready": live_ready,
        "gate": gate,
        "copy_test_ids_to_live": False,
    }


def fetch_wikibase_ids(api: str, ids: list[str]) -> dict[str, dict[str, Any]]:
    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT, "Accept": "application/json"})
    out: dict[str, dict[str, Any]] = {}
    unique = [
        i for i in dict.fromkeys(ids)
        if i and (i.startswith("Q") or i.startswith("P"))
    ]
    for offset in range(0, len(unique), _QID_BATCH):
        chunk = unique[offset: offset + _QID_BATCH]
        resp = session.get(
            api,
            params={
                "action": "wbgetentities",
                "ids": "|".join(chunk),
                "props": "labels|descriptions|claims|info",
                "languages": "en|he",
                "format": "json",
            },
            timeout=60,
        )
        resp.raise_for_status()
        entities = resp.json().get("entities") or {}
        if isinstance(entities, dict):
            out.update(entities)
        time.sleep(0.15)
    return out


def labels_from_entities(entities: dict[str, dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for eid, ent in entities.items():
        labs = entity_labels(ent)
        out[str(eid)] = labs.get("en") or labs.get("he") or ""
    return out


def load_marc_records(conn, run_id: str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT control_number, marc
            FROM run_records
            WHERE run_id::text LIKE %s
            ORDER BY control_number
            """,
            (f"{run_id}%",),
        )
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for control_number, marc in rows:
        rec = dict(marc) if isinstance(marc, dict) else {}
        rec["_control_number"] = str(control_number)
        rec.setdefault("control_number", str(control_number))
        out.append(rec)
    return out


def build_eval_item(
    studio: dict[str, Any],
    audit: dict[str, Any],
    test_snap: dict[str, Any],
    live_snap: dict[str, Any],
    outcome: dict[str, Any],
) -> dict[str, Any]:
    item = dict(studio)
    item["_local_id"] = str(studio.get("local_id") or audit.get("local_id") or "")
    item["test_wiki_snapshot"] = {k: v for k, v in test_snap.items() if k != "ref_ids"}
    item["deterministic_audit"] = {
        "status": audit.get("status"),
        "blockers": audit.get("blockers") or [],
        "warnings": audit.get("warnings") or [],
        "ready_for_live": audit.get("ready_for_live"),
        "native_statements": audit.get("native_statements"),
        "test_claims": audit.get("test_claims"),
        "test_qid": audit.get("test_qid"),
        "unresolved_local_refs": audit.get("unresolved_local_refs") or [],
    }
    item["live_existing_snapshot"] = {k: v for k, v in live_snap.items() if k != "ref_ids"}
    item["upload_outcome"] = {
        "status": outcome.get("status"),
        "qid": outcome.get("qid") or outcome.get("wikibase_id"),
        "message": outcome.get("message") or outcome.get("detail") or "",
    }
    item["native_compact"] = compact_studio_item(studio)
    return item


def _verdict_axes(row: dict[str, Any]) -> dict[str, Any]:
    inner = row.get("verdict") if isinstance(row.get("verdict"), dict) else row
    return {
        "overall": inner.get("overall"),
        "name_ok": inner.get("name_ok"),
        "type_ok": inner.get("type_ok"),
        "role_ok": inner.get("role_ok"),
        "reasoning": inner.get("reasoning") or "",
    }


def run_eval_agent(
    *,
    pipeline_dir: Path,
    state_dir: Path,
    tier_model: str,
    rpm: int,
) -> dict[str, dict[str, Any]]:
    from app.pipeline.agent_runner import _python_for, locate_eval_agent

    root = locate_eval_agent()
    py = _python_for(root)
    cmd = [
        py, "-m", "eval_agent.cli", "run",
        "--pipeline-output", str(pipeline_dir),
        "--evaluators", EVALUATOR_ID,
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
        tail = (proc.stderr or proc.stdout or "")[-2500:]
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
        lid = str(
            cand.get("_local_id")
            or cand.get("local_id")
            or row.get("record_id")
            or ""
        )
        if lid:
            verdicts[lid] = row
    return verdicts


def _resolve_tier_key(tier_model: str) -> tuple[str, str | None]:
    from app.pipeline.judge_models import resolve_tier1_model

    spec = resolve_tier1_model(tier_model)
    env_name = spec.api_key_env
    aliases = [env_name]
    if env_name == "QUBRID_API_KEY":
        aliases.append("QUABRID_API_KEY")
    for name in aliases:
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


def main() -> int:
    _load_dotenv()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--json-out", default="")
    ap.add_argument("--work-dir", default=str(_REPO / "state" / "test-wikidata-live-ready"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--local-id", action="append", default=None)
    ap.add_argument("--entity-type", default="")
    ap.add_argument("--include-skipped", action="store_true")
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--judge-blocked", action="store_true")
    ap.add_argument("--pack-only", action="store_true")
    ap.add_argument("--tier-model", default=DEFAULT_TIER_MODEL)
    ap.add_argument("--rpm", type=int, default=20)
    args = ap.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required (or pass --database-url)")

    conn = _connect(args.database_url)
    try:
        job = load_latest_test_job(conn, args.run_id)
        studio_by_id = load_studio_items(conn, args.run_id)
        try:
            marc_records = load_marc_records(conn, args.run_id)
        except Exception as exc:
            print(f"[marc] skipped ({exc})", file=sys.stderr)
            marc_records = []
    finally:
        conn.close()

    outcomes = list((job.get("result") or {}).get("outcomes") or [])
    if not outcomes:
        raise SystemExit(f"Job {job['id']} has no result.outcomes")

    test_qids = [
        str(o.get("qid") or o.get("wikibase_id") or "")
        for o in outcomes
        if str(o.get("status") or "").lower() in WRITTEN
    ]
    print(
        f"[job] {job['id']}  written_qids={len([q for q in test_qids if q])}",
        file=sys.stderr,
    )
    test_entities = fetch_entities(_TEST_API, test_qids)
    from app.pipeline.wikidata_live_native_hygiene import (  # noqa: PLC0415
        existing_qid_of,
        sanitize_studio_items_for_live,
    )

    live_qids = [existing_qid_of(item) for item in studio_by_id.values()]
    live_entities = fetch_entities(_LIVE_API, [q for q in live_qids if q])
    hygiene = sanitize_studio_items_for_live(
        list(studio_by_id.values()), live_entities=live_entities,
    )
    print(f"[hygiene] {hygiene}", file=sys.stderr)

    audits: list[dict[str, Any]] = []
    for outcome in outcomes:
        studio = studio_by_id.get(str(outcome.get("local_id") or ""))
        live_qid = str((studio or {}).get("existing_qid") or "").strip()
        audits.append(
            audit_one(
                outcome,
                studio,
                test_entities.get(str(outcome.get("qid") or outcome.get("wikibase_id") or "")),
                live_entities.get(live_qid) if live_qid else None,
            )
        )

    selected: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]] = []
    for outcome, audit in zip(outcomes, audits):
        lid = str(audit.get("local_id") or "")
        if args.local_id and lid not in args.local_id:
            continue
        if args.entity_type and str(audit.get("entity_type") or "") != args.entity_type:
            continue
        written = str(audit.get("status") or "") in WRITTEN
        if written or args.include_skipped:
            selected.append((outcome, audit, studio_by_id.get(lid)))
    if args.limit is not None:
        selected = selected[: args.limit]

    test_snaps: dict[str, dict[str, Any]] = {}
    live_snaps: dict[str, dict[str, Any]] = {}
    pending_test_ids: list[str] = []
    pending_live_ids: list[str] = []
    for outcome, audit, studio in selected:
        lid = str(audit.get("local_id") or "")
        tqid = str(outcome.get("qid") or outcome.get("wikibase_id") or "")
        tent = test_entities.get(tqid) if tqid else None
        test_snaps[lid] = compact_wikibase_entity(tent, wiki="test")
        pending_test_ids.extend(test_snaps[lid].get("ref_ids") or [])
        live_qid = str((studio or {}).get("existing_qid") or "").strip()
        live_snaps[lid] = compact_wikibase_entity(
            live_entities.get(live_qid) if live_qid else None, wiki="live",
        )
        pending_live_ids.extend(live_snaps[lid].get("ref_ids") or [])

    test_labels = labels_from_entities(fetch_wikibase_ids(_TEST_API, pending_test_ids))
    live_labels = labels_from_entities(fetch_wikibase_ids(_LIVE_API, pending_live_ids))
    for outcome, audit, studio in selected:
        lid = str(audit.get("local_id") or "")
        tqid = str(outcome.get("qid") or outcome.get("wikibase_id") or "")
        test_snaps[lid] = compact_wikibase_entity(
            test_entities.get(tqid) if tqid else None, test_labels, wiki="test",
        )
        live_qid = str((studio or {}).get("existing_qid") or "").strip()
        live_snaps[lid] = compact_wikibase_entity(
            live_entities.get(live_qid) if live_qid else None, live_labels, wiki="live",
        )

    pack_items: list[dict[str, Any]] = []
    llm_scope: list[str] = []
    for outcome, audit, studio in selected:
        lid = str(audit.get("local_id") or "")
        if studio is None:
            continue
        pack_items.append(
            build_eval_item(
                studio,
                audit,
                test_snaps.get(lid) or {},
                live_snaps.get(lid) or {},
                outcome,
            )
        )
        blockers = list(audit.get("blockers") or [])
        written = str(audit.get("status") or "") in WRITTEN
        if (
            written
            and not audit.get("skip_for_live")
            and (args.judge_blocked or not blockers)
        ):
            llm_scope.append(lid)

    work = Path(args.work_dir).expanduser().resolve() / str(args.run_id)
    pipeline_dir = work / "pipeline-output"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    (pipeline_dir / "marc_extracted.json").write_text(
        json.dumps(marc_records, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    llm_item_ids = set(llm_scope)
    llm_items = [it for it in pack_items if str(it.get("_local_id") or "") in llm_item_ids]
    (pipeline_dir / "wikidata_items.json").write_text(
        json.dumps(llm_items, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(
        f"[pack] items={len(pack_items)} llm_scope={len(llm_items)} "
        f"marc={len(marc_records)} dir={pipeline_dir}",
        file=sys.stderr,
    )
    if args.pack_only:
        print(json.dumps({
            "packed": len(pack_items),
            "llm_scope": len(llm_items),
            "dir": str(work),
        }, indent=2))
        return 0

    llm_rows: dict[str, dict[str, Any]] = {}
    judged = False
    if not args.skip_llm and llm_items:
        env_name, key = _resolve_tier_key(args.tier_model)
        if not key:
            raise SystemExit(f"missing {env_name} for tier model {args.tier_model}")
        eta_min = max(1, (len(llm_items) + args.rpm - 1) // args.rpm)
        print(
            f"[llm] {len(llm_items)} item(s) × {args.tier_model} @ {args.rpm} rpm "
            f"(~{eta_min} min)",
            file=sys.stderr,
        )
        llm_rows = run_eval_agent(
            pipeline_dir=pipeline_dir,
            state_dir=work / "eval-state",
            tier_model=args.tier_model,
            rpm=args.rpm,
        )
        judged = True
    elif args.skip_llm:
        print("[llm] skipped (--skip-llm); deterministic audit only", file=sys.stderr)

    merged: list[dict[str, Any]] = []
    for outcome, audit, studio in selected:
        lid = str(audit.get("local_id") or "")
        llm = _verdict_axes(llm_rows[lid]) if judged and lid in llm_rows else None
        row = merge_live_ready(
            audit=audit,
            llm=llm,
            judged=judged and lid in llm_item_ids,
        )
        labels = (studio or {}).get("labels") or {}
        row["label"] = audit.get("label") or labels.get("en") or labels.get("he")
        row["native"] = compact_studio_item(studio)
        row["test_wiki_snapshot"] = {
            k: v for k, v in (test_snaps.get(lid) or {}).items() if k != "ref_ids"
        }
        merged.append(row)

    written_rows = [
        r for r in merged
        if str(r.get("status") or "") in WRITTEN and not r.get("skip_for_live")
    ]
    live_ok = [r for r in written_rows if r.get("live_ready")]
    live_bad = [r for r in written_rows if not r.get("live_ready")]
    gate_counts = Counter(str(r.get("gate") or "") for r in written_rows)
    summary = {
        "job_id": job["id"],
        "run_id": args.run_id,
        "evaluator": EVALUATOR_ID,
        "tier_model": None if args.skip_llm else args.tier_model,
        "written": len(written_rows),
        "live_ready": len(live_ok),
        "not_live_ready": len(live_bad),
        "all_written_live_ready": bool(written_rows) and not live_bad,
        "gate_totals": dict(gate_counts),
        "note": (
            "live_ready means the Studio native is safe for www.wikidata.org. "
            "Test Q/P ids must not be copied (W-182 / W-183)."
        ),
        "not_ready_examples": [
            {
                "local_id": r.get("local_id"),
                "status": r.get("status"),
                "entity_type": r.get("entity_type"),
                "label": r.get("label"),
                "test_qid": r.get("test_qid"),
                "gate": r.get("gate"),
                "blockers": r.get("blockers"),
                "llm_overall": (r.get("llm") or {}).get("overall") if r.get("llm") else None,
                "reasoning": ((r.get("llm") or {}).get("reasoning") or "")[:240],
            }
            for r in live_bad[:40]
        ],
    }
    payload = {"summary": summary, "entities": merged}
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"wrote {args.json_out}", file=sys.stderr)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["all_written_live_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
