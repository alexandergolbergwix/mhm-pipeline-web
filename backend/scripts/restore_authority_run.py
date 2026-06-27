"""Restore authority_matches from a snapshot_authority_run JSON file.

Updates rows by match ``id`` (does not delete rows added after the snapshot).
Dry-run by default — pass ``--apply`` to write.

Usage:
  export DATABASE_URL="$(heroku config:get DATABASE_URL -a mhm-pipeline-web | tr -d '\\n')"
  cd backend && .venv/bin/python -m scripts.restore_authority_run \\
    --snapshot ../snapshots/authority-<run_id>-pre-reenrich.json \\
    --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db import session_scope
from app.models.run import AuthorityMatch


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


async def _restore(snapshot_path: Path, *, apply: bool) -> dict[str, int]:
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if data.get("schema") != "mhm.authority_run_snapshot.v1":
        raise SystemExit(f"unsupported snapshot schema: {data.get('schema')!r}")

    run_id = uuid.UUID(data["run"]["id"])
    matches: list[dict[str, Any]] = data.get("matches") or []
    stats = {"found": 0, "updated": 0, "missing": 0, "skipped": 0}

    async with session_scope() as db:
        for row in matches:
            mid = uuid.UUID(str(row["id"]))
            existing = (
                await db.execute(
                    select(AuthorityMatch).where(
                        AuthorityMatch.id == mid,
                        AuthorityMatch.run_id == run_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                stats["missing"] += 1
                continue
            stats["found"] += 1

            approved_by = row.get("approved_by")
            new_state = {
                "control_number": row["control_number"],
                "entity_text": row["entity_text"],
                "entity_kind": row.get("entity_kind") or "person",
                "role": row.get("role") or "",
                "matched_name": row.get("matched_name") or "",
                "mazal_id": row.get("mazal_id") or "",
                "viaf_id": row.get("viaf_id") or "",
                "wikidata_qid": row.get("wikidata_qid") or "",
                "confidence": row.get("confidence") or "low",
                "source": row.get("source") or "",
                "payload": row.get("payload") or {},
                "approved": bool(row.get("approved")),
                "approved_by": uuid.UUID(approved_by) if approved_by else None,
                "approved_at": _parse_dt(row.get("approved_at")),
            }
            if not apply:
                stats["updated"] += 1
                continue

            for key, val in new_state.items():
                setattr(existing, key, val)
            stats["updated"] += 1

        if apply:
            await db.commit()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore authority matches from snapshot")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    args = parser.parse_args()

    stats = asyncio.run(_restore(args.snapshot, apply=args.apply))
    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"{mode}: {stats}")


if __name__ == "__main__":
    main()
