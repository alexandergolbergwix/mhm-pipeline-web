"""Audit/backfill durable canonical HMO rows from live read-back snapshots."""
from __future__ import annotations
import argparse, asyncio, json, uuid, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import delete, select
from app.db import SessionLocal
from app.models.hmo_canonical_entity import HmoCanonicalEntity
from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.pipeline.hmo_canonical import normalize_live_entity

async def run(run_id: uuid.UUID | None, apply: bool) -> dict[str, object]:
    async with SessionLocal() as db:
        q = select(HmoStudioItemCache)
        if run_id is not None:
            q = q.where(HmoStudioItemCache.run_id == run_id)
        caches = (await db.execute(q)).scalars().all()
        report = {"runs": 0, "candidates": 0, "written": 0, "missing_live": 0, "invalid": [], "duplicates": []}
        for cache in caches:
            report["runs"] += 1
            seen: set[str] = set(); snapshots = []
            for raw in cache.resolved_entities or []:
                live = raw.get("canonical_live")
                if not live:
                    report["missing_live"] += 1
                    continue
                try:
                    entity = normalize_live_entity(live)
                except (TypeError, ValueError, KeyError) as exc:
                    report["invalid"].append({"run_id": str(cache.run_id), "local_id": raw.get("local_id"), "error": str(exc)})
                    continue
                if entity.local_id in seen:
                    report["duplicates"].append({"run_id": str(cache.run_id), "local_id": entity.local_id})
                    continue
                seen.add(entity.local_id); snapshots.append(entity)
            report["candidates"] += len(snapshots)
            if apply and snapshots:
                await db.execute(delete(HmoCanonicalEntity).where(HmoCanonicalEntity.run_id == cache.run_id))
                for entity in snapshots:
                    db.add(HmoCanonicalEntity(run_id=cache.run_id, local_id=entity.local_id, wikibase_id=entity.wikibase_id or "", source_fingerprint=entity.source_fingerprint, snapshot=entity.to_dict()))
                report["written"] += len(snapshots)
        if apply:
            await db.commit()
        return report

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=uuid.UUID)
    parser.add_argument("--apply", action="store_true", help="write rows; default is read-only")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.run_id, args.apply)), indent=2, sort_keys=True))
if __name__ == "__main__":
    main()
