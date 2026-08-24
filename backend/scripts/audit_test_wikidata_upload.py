#!/usr/bin/env python3
"""Read-only per-entity audit: test.wikidata.org writes vs live-upload readiness.

Live writes use Studio *natives* (live P/Q), never the remapped test wiki
ids. This script therefore:

1. Loads the latest ``wikidata_upload`` job with ``upload_target=test``.
2. For every outcome, loads the Studio-cache native and runs ``validate_item``.
3. Fetches the landed test.wikidata.org entity (when a QID was written) and
   checks it exists, has labels/claims, and does not embed live wikidata.org
   entity URIs (Rule W-185).

It never writes to Wikidata, Postgres, or caches.

    cd backend && DATABASE_URL=... .venv/bin/python -m scripts.audit_test_wikidata_upload \\
      --run-id 48ba6c13-115c-4763-bff1-c08b9031b518
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from typing import Any
from urllib.parse import urlparse

import requests

_TEST_API = "https://test.wikidata.org/w/api.php"
_LIVE_API = "https://www.wikidata.org/w/api.php"
_USER_AGENT = "MHMPipeline/1.0 (shvedbook@gmail.com) test-upload live-readiness audit"
_QID_BATCH = 50
_LIVE_WIKIDATA_URI = "http://www.wikidata.org/entity/"
_MHM_STUB_PREFIX = "MHM test stub for live "
WRITTEN = frozenset({"created", "updated", "adopted", "exists", "success"})


def count_wikibase_claims(entity: dict[str, Any] | None) -> int:
    if not isinstance(entity, dict):
        return 0
    claims = entity.get("claims") or {}
    if not isinstance(claims, dict):
        return 0
    return sum(len(v) for v in claims.values() if isinstance(v, list))


def live_value_uris(entity: dict[str, Any] | None) -> list[str]:
    """Item/quantity values that still point at www.wikidata.org (not calendarmodel)."""
    hits: list[str] = []
    if not isinstance(entity, dict):
        return hits

    def walk(node: Any, *, parent: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                walk(child, parent=str(key))
            return
        if isinstance(node, list):
            for child in node:
                walk(child, parent=parent)
            return
        if not isinstance(node, str) or _LIVE_WIKIDATA_URI not in node:
            return
        if parent == "calendarmodel":
            return
        hits.append(node)

    walk(entity.get("claims") or {}, parent="")
    return hits


def entity_has_live_wikidata_uri(entity: dict[str, Any] | None) -> bool:
    """True when an item/quantity *value* still points at www.wikidata.org.

    Wikibase time ``calendarmodel`` URIs always use the live calendar items
    even on test.wikidata.org — those are not Rule W-185 leftovers.
    """
    return bool(live_value_uris(entity))


def entity_en_description(entity: dict[str, Any] | None) -> str:
    if not isinstance(entity, dict):
        return ""
    descs = entity.get("descriptions") or {}
    en = descs.get("en") if isinstance(descs, dict) else None
    if isinstance(en, dict):
        return str(en.get("value") or "").strip()
    return str(en or "").strip()


def entity_labels(entity: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(entity, dict):
        return out
    labels = entity.get("labels") or {}
    if not isinstance(labels, dict):
        return out
    for lang, row in labels.items():
        if isinstance(row, dict):
            val = str(row.get("value") or "").strip()
        else:
            val = str(row or "").strip()
        if val:
            out[str(lang)] = val
    return out


def unresolved_local_refs(statements: list[Any]) -> list[str]:
    hits: list[str] = []
    for stmt in statements or []:
        value = getattr(stmt, "value", None)
        if value is None and isinstance(stmt, dict):
            value = stmt.get("value")
        text = str(value or "")
        if text.startswith("__LOCAL:"):
            hits.append(text)
        extra = []
        if isinstance(stmt, dict):
            extra.extend(stmt.get("qualifiers") or [])
            extra.extend(stmt.get("references") or [])
        else:
            extra.extend(getattr(stmt, "qualifiers", None) or [])
            extra.extend(getattr(stmt, "references", None) or [])
        for snak in extra:
            if not isinstance(snak, dict):
                continue
            sval = str(snak.get("value") or "")
            if sval.startswith("__LOCAL:"):
                hits.append(sval)
    return hits


def native_identifier_pids(item: Any) -> list[str]:
    found: list[str] = []
    for stmt in getattr(item, "statements", []) or []:
        pid = str(getattr(stmt, "property_id", "") or "")
        if pid in {"P214", "P8189", "P244", "P227", "P213", "P268"}:
            found.append(pid)
    return found


def _dsn_from_database_url(url: str) -> str:
    """psycopg wants postgresql:// and Heroku requires SSL."""
    parsed = urlparse(url)
    scheme = "postgresql"
    dsn = url.replace("postgres://", f"{scheme}://", 1) if parsed.scheme == "postgres" else url
    if "sslmode=" not in dsn:
        dsn += ("&" if "?" in dsn else "?") + "sslmode=require"
    return dsn


def _connect(database_url: str):
    import psycopg2  # noqa: PLC0415

    return psycopg2.connect(_dsn_from_database_url(database_url))


def load_latest_test_job(conn, run_id: str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, status, params, result, progress, finished_at
            FROM run_jobs
            WHERE run_id::text LIKE %s
              AND kind = 'wikidata_upload'
              AND COALESCE(params->>'upload_target', result->>'upload_target', '') = 'test'
            ORDER BY finished_at DESC NULLS LAST, created_at DESC
            LIMIT 1
            """,
            (f"{run_id}%",),
        )
        row = cur.fetchone()
    if not row:
        raise SystemExit(f"No test wikidata_upload job found for run {run_id}")
    job_id, status, params, result, progress, finished_at = row
    return {
        "id": job_id,
        "status": status,
        "params": params or {},
        "result": result or {},
        "progress": progress or {},
        "finished_at": str(finished_at) if finished_at else "",
    }


def load_studio_items(conn, run_id: str) -> dict[str, dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT result_items, source, approved_only
            FROM wikidata_studio_cache
            WHERE run_id::text LIKE %s
            ORDER BY
              CASE WHEN source = 'canonical' THEN 0 ELSE 1 END,
              CASE WHEN approved_only THEN 0 ELSE 1 END,
              built_at DESC
            LIMIT 1
            """,
            (f"{run_id}%",),
        )
        row = cur.fetchone()
    if not row:
        return {}
    items, _source, _approved = row
    out: dict[str, dict[str, Any]] = {}
    for raw in items or []:
        if isinstance(raw, dict) and raw.get("local_id"):
            out[str(raw["local_id"])] = raw
    return out


def native_from_studio(raw: dict[str, Any]):
    from converter.wikidata.item_models import WikidataItem, WikidataStatement
    from converter.wikidata.property_mapping import PRECISION_YEAR

    stmts: list[WikidataStatement] = []
    for s in raw.get("statements") or []:
        if not isinstance(s, dict):
            continue
        pid = str(s.get("property_id") or s.get("property") or "").strip()
        if not pid:
            continue
        precision = s.get("precision")
        try:
            precision_i = int(precision) if precision is not None else PRECISION_YEAR
        except (TypeError, ValueError):
            precision_i = PRECISION_YEAR
        stmts.append(
            WikidataStatement(
                property_id=pid,
                value=s.get("value"),
                value_type=str(s.get("value_type") or "string"),
                qualifiers=list(s.get("qualifiers") or []),
                references=list(s.get("references") or []),
                precision=precision_i,
                language=str(s.get("language") or "he"),
                unit=str(s.get("unit") or ""),
                rank=str(s.get("rank") or "normal"),
            )
        )
    qid = str(raw.get("existing_qid") or "").strip() or None
    if qid and not qid.startswith("Q"):
        qid = None
    aliases_raw = raw.get("aliases") or {}
    aliases: dict[str, list[str]] = {}
    if isinstance(aliases_raw, dict):
        for lang, vals in aliases_raw.items():
            if isinstance(vals, list):
                aliases[str(lang)] = [str(v) for v in vals if str(v).strip()]
            elif vals:
                aliases[str(lang)] = [str(vals)]
    return WikidataItem(
        labels={str(k): str(v) for k, v in (raw.get("labels") or {}).items()},
        descriptions={
            str(k): str(v) for k, v in (raw.get("descriptions") or {}).items()
        },
        aliases=aliases,
        statements=stmts,
        existing_qid=qid,
        entity_type=str(raw.get("entity_type") or ""),
        semantic_type=str(raw.get("semantic_type") or ""),
        local_id=str(raw.get("local_id") or ""),
        records=[str(r) for r in (raw.get("records") or raw.get("record_ids") or [])],
        authority_evidence=list(raw.get("authority_evidence") or []),
        work_candidate_evidence=list(raw.get("work_candidate_evidence") or []),
        heading_mismatch=raw.get("heading_mismatch"),
    )


def fetch_entities(api: str, qids: list[str]) -> dict[str, dict[str, Any]]:
    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT, "Accept": "application/json"})
    out: dict[str, dict[str, Any]] = {}
    unique = [q for q in dict.fromkeys(qids) if q and q.startswith("Q")]
    for i in range(0, len(unique), _QID_BATCH):
        chunk = unique[i : i + _QID_BATCH]
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


def audit_one(
    outcome: dict[str, Any],
    studio: dict[str, Any] | None,
    test_entity: dict[str, Any] | None,
    live_entity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from converter.wikidata.item_validator import validate_item

    status = str(outcome.get("status") or "").lower()
    local_id = str(outcome.get("local_id") or "")
    test_qid = str(outcome.get("qid") or outcome.get("wikibase_id") or "").strip()
    blockers: list[str] = []
    warnings: list[str] = []

    if status in {"failed", "blocked"}:
        blockers.append(f"upload_{status}")
    elif status == "skipped":
        warnings.append("skipped_not_written")

    native = native_from_studio(studio) if studio else None
    if studio is None:
        blockers.append("missing_studio_cache_row")

    validator_errors: list[str] = []
    validator_warnings: list[str] = []
    native_stmt_n = 0
    local_refs: list[str] = []
    skip_for_live = False
    if native is not None:
        native_stmt_n = len(native.statements or [])
        issues = validate_item(native)
        validator_errors = [i.code for i in issues if i.severity == "error"]
        validator_warnings = [i.code for i in issues if i.severity == "warning"]
        if validator_errors:
            blockers.extend(f"validator:{c}" for c in validator_errors)
        if validator_warnings:
            warnings.extend(f"validator_warn:{c}" for c in validator_warnings)
        local_refs = unresolved_local_refs(native.statements)
        if local_refs and status in WRITTEN:
            warnings.append("studio_cache_has__LOCAL")
        et = str(native.entity_type or "")
        skip_for_live = et == "person" and not native_identifier_pids(native)
        if (
            not skip_for_live
            and et == "person"
            and status in WRITTEN
            and not native_identifier_pids(native)
        ):
            blockers.append("person_no_identifier")
        if et == "person" and native.existing_qid:
            from app.pipeline.wikidata_duplicate_probe import (  # noqa: PLC0415
                person_heading_conflicts_live_label,
            )

            live_labs = entity_labels(live_entity)
            if live_labs and person_heading_conflicts_live_label(
                native,
                live_en=live_labs.get("en") or "",
                live_he=live_labs.get("he") or "",
            ):
                blockers.append(f"identity_clash:{native.existing_qid}")
        if et == "manuscript" and status in WRITTEN:
            pids = {str(s.property_id) for s in native.statements}
            if "P31" not in pids:
                blockers.append("manuscript_missing_P31")
            if "P217" not in pids and "P3959" not in pids:
                blockers.append("manuscript_missing_shelfmark_or_catalog")

    test_claim_n = 0
    if status in WRITTEN:
        if not test_qid:
            blockers.append("written_without_qid")
        elif not test_entity or "missing" in test_entity:
            blockers.append("test_entity_missing")
        else:
            test_claim_n = count_wikibase_claims(test_entity)
            labels = entity_labels(test_entity)
            if not labels:
                blockers.append("test_entity_no_label")
            if test_claim_n == 0:
                blockers.append("test_entity_no_claims")
            if native_stmt_n and test_claim_n < native_stmt_n:
                gap = f"test_claims_lt_native:{test_claim_n}<{native_stmt_n}"
                if status in {"created", "updated", "adopted"}:
                    blockers.append(gap)
                else:
                    warnings.append(gap)
            if live_value_uris(test_entity):
                blockers.append("live_wikidata_uri_on_test")
            desc = entity_en_description(test_entity)
            if desc.startswith(_MHM_STUB_PREFIX):
                warnings.append("item_is_mhm_class_stub")

    ready = not blockers
    return {
        "local_id": local_id,
        "status": status,
        "test_qid": test_qid or None,
        "entity_type": (native.entity_type if native else None) or outcome.get("entity_type"),
        "label": (studio or {}).get("labels", {}).get("en")
        or (studio or {}).get("labels", {}).get("he")
        or outcome.get("label"),
        "native_statements": native_stmt_n,
        "test_claims": test_claim_n,
        "unresolved_local_refs": local_refs,
        "blockers": blockers,
        "warnings": warnings,
        "skip_for_live": skip_for_live,
        "ready_for_live": ready and status in WRITTEN and not skip_for_live,
        "test_url": (
            f"https://test.wikidata.org/wiki/{test_qid}" if test_qid else None
        ),
    }


def summarise(rows: list[dict[str, Any]], job: dict[str, Any]) -> dict[str, Any]:
    counts = Counter(str(r["status"]) for r in rows)
    ready = [r for r in rows if r["ready_for_live"]]
    not_ready = [r for r in rows if not r["ready_for_live"]]
    written = [r for r in rows if r["status"] in WRITTEN]
    blocker_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    for row in rows:
        for code in row["blockers"]:
            blocker_counts[code.split(":")[0]] += 1
        for code in row["warnings"]:
            warning_counts[code.split(":")[0]] += 1
    return {
        "job_id": job["id"],
        "job_status": job["status"],
        "finished_at": job["finished_at"],
        "outcome_counts": dict(counts),
        "written": len(written),
        "ready_for_live": len(ready),
        "not_ready": len(not_ready),
        "blocker_totals": dict(blocker_counts),
        "warning_totals": dict(warning_counts),
        "all_written_ready": bool(written) and all(r["ready_for_live"] for r in written),
        "created_updated_adopted_ready": all(
            r["ready_for_live"]
            for r in rows
            if r["status"] in {"created", "updated", "adopted"}
        ),
        "not_ready_examples": [
            {
                "local_id": r["local_id"],
                "status": r["status"],
                "entity_type": r["entity_type"],
                "label": r["label"],
                "test_qid": r["test_qid"],
                "blockers": r["blockers"],
            }
            for r in not_ready[:25]
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required (or pass --database-url)")

    conn = _connect(args.database_url)
    try:
        job = load_latest_test_job(conn, args.run_id)
        studio_by_id = load_studio_items(conn, args.run_id)
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

    rows = []
    for o in outcomes:
        studio = studio_by_id.get(str(o.get("local_id") or ""))
        live_qid = str((studio or {}).get("existing_qid") or "").strip()
        rows.append(
            audit_one(
                o,
                studio,
                test_entities.get(str(o.get("qid") or o.get("wikibase_id") or "")),
                live_entities.get(live_qid) if live_qid else None,
            )
        )
    report = summarise(rows, job)
    report["note"] = (
        "ready_for_live means the Studio native would pass the live write gate "
        "and the test wiki entity landed with a full claim set. Test Q-ids must "
        "not be copied to www.wikidata.org (Rules W-182 / W-183)."
    )

    if args.json_out:
        payload = {"summary": report, "entities": rows}
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"wrote {args.json_out}", file=sys.stderr)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["all_written_ready"] or report["outcome_counts"].get("failed") or report["outcome_counts"].get("blocked"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
