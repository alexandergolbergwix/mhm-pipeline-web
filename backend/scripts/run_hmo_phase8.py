"""Run the Phase 8 HMO test sweep and optional live idempotency pass.

The default mode is read-only: it runs the focused backend/frontend suites and
writes a JSON report. The live workflow is deliberately double-gated because
it creates and updates entities on the shared HMO Wikibase instance::

    HMO_PHASE8_LIVE_WRITES=1 \
      python -m scripts.run_hmo_phase8 --run-id <uuid> --live \
      --confirm-live-writes

The live report includes schema/item mapping counts and representative QIDs /
PIDs, making it suitable for attaching to a PR or release record.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.db import session_scope
from app.models.run import Run
from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.wikibase_entity_mapping import ENTITY_KIND_INSTANCE, WikibaseEntityMapping
from app.pipeline import hmo_item_build, hmo_item_upload
from app.pipeline.hmo_schema_bootstrap import bootstrap_schema, schema_status
from app.pipeline.rdf_build import ensure_ttl_on_disk, rdf_output_path_for_run
from app.services.wikibase_credentials import build_server_wikibase_writer

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_CONFIRMATION = "1"


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    output_tail: str


def _run_command(command: list[str], cwd: Path) -> CommandResult:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines = completed.stdout.splitlines()
    return CommandResult(command, completed.returncode, "\n".join(lines[-40:]))


def run_test_sweep() -> dict[str, Any]:
    """Run the focused backend and Phase 7 browser suites."""
    commands = [
        [sys.executable, "-m", "pytest", "tests", "-k", "hmo"],
        ["yarn", "test:e2e", "hmo-wikibase-studio.spec.ts"],
    ]
    results: list[CommandResult] = []
    for command in commands:
        cwd = REPO_ROOT / "frontend" if command[0] == "yarn" else REPO_ROOT / "backend"
        result = _run_command(command, cwd)
        results.append(result)
        if result.returncode != 0:
            break
    return {
        "passed": bool(results) and all(result.returncode == 0 for result in results),
        "commands": [
            {
                "command": result.command,
                "returncode": result.returncode,
                "output_tail": result.output_tail,
            }
            for result in results
        ],
    }


async def _mapping_snapshot(db: Any) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(
                WikibaseEntityMapping.entity_kind,
                func.count(WikibaseEntityMapping.id),
            ).group_by(WikibaseEntityMapping.entity_kind)
        )
    ).all()
    samples = (
        await db.execute(
            select(
                WikibaseEntityMapping.entity_kind,
                WikibaseEntityMapping.ontology_uri,
                WikibaseEntityMapping.wikibase_id,
            )
            .where(WikibaseEntityMapping.run_id.is_(None))
            .order_by(WikibaseEntityMapping.entity_kind, WikibaseEntityMapping.ontology_uri)
            .limit(10)
        )
    ).all()
    return {
        "counts": {kind: count for kind, count in rows},
        "samples": [
            {"entity_kind": kind, "ontology_uri": uri, "wikibase_id": wikibase_id}
            for kind, uri, wikibase_id in samples
        ],
    }


async def _item_status_snapshot(db: Any, run_id: uuid.UUID) -> dict[str, Any]:
    cache = (
        await db.execute(
            select(HmoStudioItemCache).where(HmoStudioItemCache.run_id == run_id)
        )
    ).scalar_one_or_none()
    uploaded_count = await db.scalar(
        select(func.count(WikibaseEntityMapping.id)).where(
            WikibaseEntityMapping.run_id == run_id,
            WikibaseEntityMapping.entity_kind == ENTITY_KIND_INSTANCE,
        )
    )
    return {
        "build_present": cache is not None,
        "entity_count": cache.entity_count if cache else 0,
        "deferred_link_count": cache.deferred_link_count if cache else 0,
        "uploaded_count": uploaded_count or 0,
        "built_at": cache.built_at.isoformat() if cache else None,
    }


async def run_live_pass(run_id: uuid.UUID) -> dict[str, Any]:
    """Execute the ordered live pass using the production pipeline functions."""
    async with session_scope() as db:
        run = await db.get(Run, run_id)
        if run is None:
            raise ValueError(f"run not found: {run_id}")

        before = await _mapping_snapshot(db)
        dry_schema = await bootstrap_schema(db, writer=None, dry_run=True)
        schema_status_before = await schema_status(db)
        writer = build_server_wikibase_writer()
        live_schema = await bootstrap_schema(db, writer=writer, dry_run=False)
        await db.commit()
        schema_status_after = await schema_status(db)

        ttl_path = rdf_output_path_for_run(str(run_id))
        await ensure_ttl_on_disk(ttl_path, run_id, db)
        build = await hmo_item_build.build_items_for_run(db, run_id, ttl_path)
        await db.commit()
        dry_upload = await hmo_item_upload.upload_items_for_run(
            db,
            run_id,
            writer=None,
            dry_run=True,
        )
        live_upload = await hmo_item_upload.upload_items_for_run(
            db,
            run_id,
            writer=writer,
            dry_run=False,
        )
        await db.commit()

        live_schema_repeat = await bootstrap_schema(db, writer=writer, dry_run=False)
        repeat_upload = await hmo_item_upload.upload_items_for_run(
            db,
            run_id,
            writer=writer,
            dry_run=False,
        )
        await db.commit()
        item_status = await _item_status_snapshot(db, run_id)
        after = await _mapping_snapshot(db)

    return {
        "run_id": str(run_id),
        "schema": {
            "before": schema_status_before.__dict__,
            "dry_run": {
                "would_create": dry_schema.would_create,
                "skipped": dry_schema.skipped,
            },
            "live": {
                "created": live_schema.created,
                "skipped": live_schema.skipped,
                "failed": live_schema.failed,
            },
            "repeat_live": {
                "created": live_schema_repeat.created,
                "skipped": live_schema_repeat.skipped,
                "failed": live_schema_repeat.failed,
            },
            "after": schema_status_after.__dict__,
        },
        "items": {
            "build": {
                "entity_count": build.entity_count,
                "deferred_link_count": build.deferred_link_count,
                "from_cache": build.from_cache,
            },
            "dry_run_upload": {
                "created": dry_upload.created,
                "skipped": dry_upload.skipped,
                "unresolved_links": dry_upload.unresolved_links,
            },
            "live_upload": {
                "created": live_upload.created,
                "updated": live_upload.updated,
                "skipped": live_upload.skipped,
                "failed": live_upload.failed,
                "linked": live_upload.linked,
                "unresolved_links": live_upload.unresolved_links,
            },
            "repeat_live_upload": {
                "created": repeat_upload.created,
                "updated": repeat_upload.updated,
                "skipped": repeat_upload.skipped,
                "failed": repeat_upload.failed,
                "linked": repeat_upload.linked,
                "unresolved_links": repeat_upload.unresolved_links,
            },
            "item_status": item_status,
        },
        "mapping_table": {"before": before, "after": after},
        "idempotent": (
            live_schema_repeat.created == 0
            and repeat_upload.created == 0
            and repeat_upload.updated == 0
            and repeat_upload.failed == 0
            and repeat_upload.unresolved_links == 0
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=uuid.UUID)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live-writes", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    if args.live and args.run_id is None:
        parser.error("--run-id is required with --live")
    if args.live and (
        not args.confirm_live_writes
        or os.environ.get("HMO_PHASE8_LIVE_WRITES") != LIVE_CONFIRMATION
    ):
        parser.error(
            "live writes require --confirm-live-writes and "
            "HMO_PHASE8_LIVE_WRITES=1"
        )

    report: dict[str, Any] = {
        "phase": 8,
        "started_at": datetime.now(UTC).isoformat(),
        "live": args.live,
    }
    if not args.skip_tests:
        report["test_sweep"] = run_test_sweep()
        if not report["test_sweep"]["passed"]:
            report["ready"] = False
            return _write_report(report, args.report)

    if args.live:
        report["live_pass"] = asyncio.run(run_live_pass(args.run_id))
        report["ready"] = bool(report["live_pass"]["idempotent"])
    else:
        report["ready"] = bool(report.get("test_sweep", {}).get("passed", True))
        report["next_step"] = (
            "Set HMO_PHASE8_LIVE_WRITES=1 and pass --live "
            "--confirm-live-writes with a small authorized run to execute writes."
        )
    return _write_report(report, args.report)


def _write_report(report: dict[str, Any], path: Path | None) -> int:
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if path is None:
        print(payload)
    else:
        path.write_text(payload + "\n", encoding="utf-8")
        print(json.dumps({"report": str(path), "ready": report["ready"]}))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
