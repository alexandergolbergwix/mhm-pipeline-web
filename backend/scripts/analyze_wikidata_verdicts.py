#!/usr/bin/env python3
"""Send only non-passing Wikidata CSV or JSON rows to the read-only Codex CLI."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_TEXT = 600
MAX_LIST = 8
DROP_KEYS = {"raw", "prompt", "trace", "references", "tool_calls"}


def _compact(value: object) -> object:
    if isinstance(value, str):
        value = " ".join(value.split())
        return value if len(value) <= MAX_TEXT else f"{value[:MAX_TEXT]}…"
    if isinstance(value, list):
        return [_compact(item) for item in value[:MAX_LIST]]
    if isinstance(value, dict):
        return {
            str(key): _compact(item)
            for key, item in list(value.items())[:24]
            if str(key).lower() not in DROP_KEYS and item not in (None, "", [], {})
        }
    return value


def _json_cell(row: dict[str, str], field: str) -> object:
    raw = row.get(field, "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _status(verdict: object) -> str:
    if isinstance(verdict, str):
        return {"partial": "partial", "fail": "fail", "failed": "fail"}.get(
            verdict.strip().lower(), ""
        )
    if isinstance(verdict, dict):
        for key in ("overall", "overall_verdict", "verdict", "status", "result"):
            status = _status(verdict.get(key))
            if status:
                return status
    return ""


def _compact_item(item: dict[str, object], verdict: object, status: str) -> dict[str, object]:
    labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
    descriptions = item.get("descriptions") if isinstance(item.get("descriptions"), dict) else {}
    return _compact(
        {
            "local_id": item.get("local_id")
            or item.get("_local_id")
            or item.get("entity")
            or item.get("id"),
            "entity_type": item.get("entity_type") or item.get("sub_type"),
            "labels": labels,
            "descriptions": descriptions,
            "existing_qid": item.get("existing_qid"),
            "statements": item.get("statements") or [],
            "validation_issues": item.get("validation_issues") or [],
            "marc_context": item.get("marc_context") or item.get("_marc_context") or {},
            "verdict": verdict
            if isinstance(verdict, dict)
            else {"overall": status, "text": verdict},
        }
    )


def _short(value: object, limit: int = 220) -> object:
    if isinstance(value, str):
        normalised = " ".join(value.split())
        return normalised if len(normalised) <= limit else f"{normalised[:limit]}…"
    if isinstance(value, list):
        return [_short(item, limit) for item in value[:4]]
    if isinstance(value, dict):
        return {
            str(key): _short(item, limit)
            for key, item in list(value.items())[:8]
            if str(key).lower() not in DROP_KEYS and item not in (None, "", [], {})
        }
    return value


def _issue_codes(item: dict[str, object]) -> list[str]:
    return sorted(
        str(issue.get("code"))
        for issue in item.get("validation_issues") or []
        if isinstance(issue, dict) and issue.get("code")
    )


def _signature(item: dict[str, object]) -> tuple[str, str, str, str, str, str]:
    verdict = item.get("verdict") if isinstance(item.get("verdict"), dict) else {}
    return (
        _status(verdict),
        str(item.get("entity_type") or "unknown"),
        str(verdict.get("name_ok") or ""),
        str(verdict.get("type_ok") or ""),
        str(verdict.get("role_ok") or ""),
        ",".join(_issue_codes(item)[:4]),
    )


def _sample(item: dict[str, object]) -> dict[str, object]:
    statements: list[dict[str, object]] = []
    for statement in item.get("statements") or []:
        if not isinstance(statement, dict):
            continue
        statements.append(
            {
                "property": statement.get("property") or statement.get("property_id"),
                "value": _short(statement.get("value") or statement.get("value_id") or "", 120),
            }
        )
        if len(statements) == 4:
            break
    marc = item.get("marc_context") if isinstance(item.get("marc_context"), dict) else {}
    verdict = item.get("verdict") if isinstance(item.get("verdict"), dict) else {}
    return {
        "local_id": item.get("local_id"),
        "entity_type": item.get("entity_type"),
        "labels": _short(item.get("labels") or {}, 180),
        "descriptions": _short(item.get("descriptions") or {}, 180),
        "existing_qid": item.get("existing_qid"),
        "statements": statements,
        "validation_codes": _issue_codes(item),
        "marc": {
            key: _short(marc[key])
            for key in ("title", "authors", "contributors", "contents", "shelfmark", "provenance")
            if marc.get(key)
        },
        "verdict": {
            key: _short(verdict.get(key), 260)
            for key in ("overall", "name_ok", "type_ok", "role_ok", "reasoning")
            if verdict.get(key) not in (None, "")
        },
    }


def _analysis(
    rows: list[dict[str, object]],
    *,
    max_examples: int,
    max_clusters: int,
) -> dict[str, object]:
    grouped: dict[tuple[str, str, str, str, str, str], list[dict[str, object]]] = {}
    for item in rows:
        grouped.setdefault(_signature(item), []).append(item)
    ordered = sorted(grouped.items(), key=lambda entry: (-len(entry[1]), entry[0]))
    clusters = []
    for signature, items in ordered[:max_clusters]:
        overall, entity_type, name_ok, type_ok, role_ok, validation_codes = signature
        clusters.append(
            {
                "count": len(items),
                "signature": {
                    "overall": overall,
                    "entity_type": entity_type,
                    "name_ok": name_ok,
                    "type_ok": type_ok,
                    "role_ok": role_ok,
                    "validation_codes": validation_codes,
                },
                "examples": [_sample(item) for item in items[:max_examples]],
            }
        )
    return {
        "selected_non_passing": len(rows),
        "cluster_count": len(grouped),
        "omitted_clusters": max(0, len(grouped) - len(clusters)),
        "clusters": clusters,
    }


def _rows(path: Path) -> list[dict[str, object]]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            candidates = payload
        elif isinstance(payload, dict):
            candidates = (
                payload.get("items") or payload.get("verdicts") or payload.get("results") or []
            )
        else:
            candidates = []
        selected = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            verdict = item.get("ai_verdict") or item.get("verdict") or item
            status = _status(verdict)
            if status in {"partial", "fail"}:
                selected.append(_compact_item(item, verdict, status))
        return selected

    with path.open(newline="", encoding="utf-8-sig") as stream:
        selected: list[dict[str, object]] = []
        for row in csv.DictReader(stream):
            status = _status(row.get("ai_verdict_overall") or "")
            if status not in {"partial", "fail"}:
                continue
            verdict = _json_cell(row, "ai_verdict_json") or {
                "overall": status,
                "reasoning": row.get("ai_verdict_reasoning"),
            }
            selected.append(
                _compact_item(
                    {
                        "local_id": row.get("local_id"),
                        "entity_type": row.get("entity_type"),
                        "labels": {"en": row.get("label_en"), "he": row.get("label_he")},
                        "descriptions": {
                            "en": row.get("description_en"),
                            "he": row.get("description_he"),
                        },
                        "existing_qid": row.get("existing_qid"),
                        "statements": _json_cell(row, "statements_json"),
                        "validation_issues": _json_cell(row, "validation_issues_json"),
                        "marc_context": _json_cell(row, "marc_context_json"),
                    },
                    verdict,
                    status,
                )
            )
        return selected


def _prompt(analysis: dict[str, object]) -> str:
    payload = json.dumps(analysis, ensure_ascii=False, separators=(",", ":"))
    return (
        "Inspect the repository and these non-passing Wikidata evaluator rows. "
        "Do not edit files. Group recurring causes with counts, identify the "
        "exact builder/rubric files to change, and give a concise fix and test "
        "plan. Use only evidence below and repository code; keep under 600 words.\n"
        f"CLUSTERS:\n{payload}"
    )


def _run_codex(analysis: dict[str, object], repo: Path, binary: str, timeout: int) -> str:
    result = subprocess.run(  # noqa: S603 -- explicit Codex CLI argv, never shell-evaluated
        [binary, "exec", "--ephemeral", "--sandbox", "read-only", "-C", str(repo), "-"],
        input=_prompt(analysis),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise RuntimeError(f"Codex exited {result.returncode}: {detail}")
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--dump-json", type=Path)
    parser.add_argument("--no-codex", action="store_true")
    parser.add_argument("--max-examples", type=int, default=1)
    parser.add_argument("--max-clusters", type=int, default=64)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--codex-bin",
        default=os.environ.get("CODEX_BIN") or shutil.which("codex") or "codex",
    )
    args = parser.parse_args()
    try:
        rows = _rows(args.export.expanduser())
    except (OSError, csv.Error, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    analysis = _analysis(
        rows,
        max_examples=max(1, args.max_examples),
        max_clusters=max(1, args.max_clusters),
    )
    if args.dump_json:
        args.dump_json.expanduser().write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if not rows:
        print("No partial/fail verdicts found; Codex not invoked.")
        return 0
    print(f"Selected {len(rows)} non-passing verdicts.", file=sys.stderr)
    if args.no_codex:
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
        return 0
    try:
        answer = _run_codex(analysis, args.repo.expanduser(), args.codex_bin, args.timeout)
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.report:
        args.report.expanduser().write_text(answer + "\n", encoding="utf-8")
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
