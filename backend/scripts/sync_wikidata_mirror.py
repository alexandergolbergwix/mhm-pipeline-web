"""Import, refresh, and query public Wikidata candidates without wiki writes."""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def report(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, default=str), flush=True)


async def run(args) -> None:
    from app.db import SessionLocal, engine
    from app.models.wikidata_mirror import (
        WikidataMirrorEntity,
        WikidataMirrorFact,
        WikidataMirrorScope,
    )
    from app.pipeline.inference_cache import canonical_hash
    from app.pipeline.wikidata_mirror import filter_entities, store_entities, validate_entity
    from app.pipeline.wikidata_mirror_sync import discover_candidates, refresh_qids

    if args.database_file:
        async with engine.begin() as connection:
            for model in (WikidataMirrorEntity, WikidataMirrorFact, WikidataMirrorScope):
                await connection.run_sync(model.__table__.create, checkfirst=True)
    summary = {"public_writes": 0, "coverage": "selected_candidates_only"}
    if args.snapshot:
        data = json.loads(args.snapshot.read_text())
        if not isinstance(data, dict) or not isinstance(data.get("entities"), dict):
            raise ValueError("Use a complete snapshot with entities and completed_at.")
        observed = datetime.fromisoformat(data.get("started_at") or data["completed_at"])
        entities = []
        redirects = {}
        for requested, document in data["entities"].items():
            validate_entity(document)
            if requested != document["id"]:
                redirects[requested] = document["id"]
                continue
            entities.append(document)
        source = "snapshot:" + canonical_hash(
            {"entities": data["entities"], "completed_at": data["completed_at"]}
        )
        scope_key = canonical_hash({"source": source})
        async with SessionLocal() as db:
            previous = await db.get(WikidataMirrorScope, scope_key)
            cursor = int(previous.progress.get("visited", 0)) if previous else 0
            stored = previous.stored_count if previous else 0
            await db.merge(
                WikidataMirrorScope(
                    scope_key=scope_key,
                    source=source,
                    status="in_progress",
                    requested_count=len(data["entities"]),
                    stored_count=stored,
                    progress={
                        "visited": cursor,
                        "coverage": "input_snapshot_only",
                        "redirects": redirects,
                    },
                    updated_at=datetime.now(UTC),
                )
            )
            await db.commit()
        for start in range(cursor, len(entities), 50):
            async with SessionLocal() as db:
                stored += await store_entities(
                    db, entities[start : start + 50], observed_at=observed, source=source
                )
                scope = await db.get(WikidataMirrorScope, scope_key)
                scope.stored_count = stored
                scope.progress = {
                    "visited": min(start + 50, len(entities)),
                    "coverage": "input_snapshot_only",
                    "redirects": redirects,
                }
                scope.updated_at = datetime.now(UTC)
                await db.commit()
            report(
                {
                    "phase": "import",
                    "accepted": stored,
                    "visited": min(start + 50, len(entities)),
                    "total": len(entities),
                }
            )
        async with SessionLocal() as db:
            await db.merge(
                WikidataMirrorScope(
                    scope_key=scope_key,
                    source=source,
                    status="partial" if redirects else "selected_qids_complete",
                    requested_count=len(data["entities"]),
                    stored_count=stored,
                    progress={
                        "coverage": "input_snapshot_only",
                        "qids": sorted(document["id"] for document in entities),
                        "redirects": redirects,
                        "visited": len(entities),
                    },
                    updated_at=datetime.now(UTC),
                )
            )
            await db.commit()
        summary["imported"] = stored
        summary["redirects_require_review"] = redirects
    qids = []
    if args.qids:
        qids = json.loads(args.qids.read_text())
        if not isinstance(qids, list) or not all(isinstance(qid, str) for qid in qids):
            raise ValueError("The QID file must contain a JSON list of strings.")
    if args.items:
        data = json.loads(args.items.read_text())
        items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ValueError("The item file must contain a list or an items object.")
        discovery = await discover_candidates(
            SessionLocal,
            items,
            max_pages=args.max_pages,
            max_queries=args.max_queries,
            refresh=args.refresh,
            provider=args.discovery_provider,
            on_progress=report,
        )
        qids.extend(discovery["qids"])
        summary["discovery"] = {key: value for key, value in discovery.items() if key != "qids"}
    if qids:
        summary["refresh"] = await refresh_qids(
            SessionLocal, qids, refresh=args.refresh, on_progress=report
        )
    if args.filter:
        filters = []
        for term in args.filter:
            pid, separator, value = term.partition("=")
            if not separator or not value:
                raise ValueError("Each filter needs PROPERTY=VALUE.")
            filters.append((pid, value))
        async with SessionLocal() as db:
            rows = await filter_entities(
                db, filters, limit=args.limit, include_stale=args.include_stale
            )
            summary["matches"] = [
                {
                    "qid": row.qid,
                    "revision": row.revision,
                    "observed_at": row.observed_at.isoformat(),
                    "link_policy": "verify_before_reference",
                    **({"document": row.document} if args.with_document else {}),
                }
                for row in rows
            ]
            await db.rollback()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(summary, ensure_ascii=False, default=str, indent=2) + "\n"
        )
        report({"output": str(args.output), "public_writes": 0})
    else:
        report(summary)
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--database-file", type=Path)
    target.add_argument("--database-from-heroku", action="store_true")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--items", type=Path)
    parser.add_argument("--discovery-provider", choices=("wikidata", "qlever"), default="wikidata")
    parser.add_argument("--qids", type=Path)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--max-queries", type=int, default=100)
    parser.add_argument("--filter", action="append", default=[])
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--include-stale", action="store_true")
    parser.add_argument("--with-document", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not (args.snapshot or args.qids or args.items or args.filter):
        parser.error("Select a snapshot import, a refresh scope, or a local filter.")
    if (
        not 1 <= args.limit <= 500
        or not 1 <= args.max_pages <= 100
        or not 1 <= args.max_queries <= 10000
    ):
        parser.error("The limits are outside the allowed range.")
    if args.database_file:
        args.database_file = args.database_file.resolve()
        args.database_file.parent.mkdir(parents=True, exist_ok=True)
        os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + str(args.database_file)
        os.environ["PG_BOUNCER_URL"] = ""
    elif args.database_from_heroku:
        heroku = shutil.which("heroku")
        if not heroku:
            parser.error("The Heroku CLI is unavailable.")
        result = subprocess.run(  # noqa: S603 -- resolved executable and fixed read-only arguments
            [heroku, "config:get", "DATABASE_URL", "--app", "mhm-pipeline-web"],
            capture_output=True,
            text=True,
            timeout=45,
            check=True,
        )
        os.environ["DATABASE_URL"] = result.stdout.strip()
        os.environ["PG_BOUNCER_URL"] = ""
    try:
        asyncio.run(run(args))
    except Exception as error:
        report({"status": "stopped", "error_type": type(error).__name__, "public_writes": 0})
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
