"""Snapshot authority_matches for one run (full row + payload) before re-enrich.

Usage (production):
  export DATABASE_URL="$(heroku config:get DATABASE_URL -a mhm-pipeline-web | tr -d '\\n')"
  cd backend && .venv/bin/python -m scripts.snapshot_authority_run \\
    --run-id <uuid> \\
    --output ../snapshots/authority-<run_id>-pre-reenrich.json \\
    --note "before homonym routing re-enrich"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db import session_scope
from app.models.run import AuthorityMatch, Run
from app.pipeline.run import serialise_match


def _row_to_snapshot(m: AuthorityMatch) -> dict[str, Any]:
    base = serialise_match(m)
    base["created_at"] = m.created_at.isoformat() if m.created_at else None
    return base


async def _snapshot(
    run_id: uuid.UUID,
    *,
    note: str,
) -> dict[str, Any]:
    async with session_scope() as db:
        run = await db.get(Run, run_id)
        if run is None:
            raise SystemExit(f"run not found: {run_id}")
        rows = (
            await db.execute(
                select(AuthorityMatch)
                .where(AuthorityMatch.run_id == run_id)
                .order_by(AuthorityMatch.control_number, AuthorityMatch.entity_text)
            )
        ).scalars().all()
        return {
            "schema": "mhm.authority_run_snapshot.v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "note": note,
            "run": {
                "id": str(run.id),
                "project_id": str(run.project_id),
                "name": run.name,
                "status": run.status,
                "record_count": run.record_count,
                "match_count": run.match_count,
            },
            "match_count": len(rows),
            "matches": [_row_to_snapshot(m) for m in rows],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot authority matches for a run")
    parser.add_argument("--run-id", type=uuid.UUID, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--note", default="pre re-enrich backup")
    args = parser.parse_args()

    payload = asyncio.run(_snapshot(args.run_id, note=args.note))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {payload['match_count']} matches → {args.output}")


if __name__ == "__main__":
    main()
