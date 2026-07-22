"""Fail-closed readiness gate for canonical HMO migration."""
from __future__ import annotations
import argparse, asyncio, json, sys, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import SessionLocal
from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.hmo_canonical_entity import HmoCanonicalEntity
from app.pipeline.hmo_canonical import normalize_live_entity
from sqlalchemy import select

async def inspect(run_id: uuid.UUID | None) -> dict[str, object]:
    async with SessionLocal() as db:
        q = select(HmoStudioItemCache)
        if run_id: q = q.where(HmoStudioItemCache.run_id == run_id)
        caches = (await db.execute(q)).scalars().all()
        result = {"runs": len(caches), "items": 0, "canonical_rows": 0, "missing": 0, "invalid": [], "duplicates": [], "ready": False}
        for cache in caches:
            rows = (await db.execute(select(HmoCanonicalEntity).where(HmoCanonicalEntity.run_id == cache.run_id))).scalars().all()
            result["canonical_rows"] += len(rows)
            row_ids = {row.local_id for row in rows}
            for raw in cache.resolved_entities or []:
                result["items"] += 1
                local_id = str(raw.get("local_id") or "")
                live = raw.get("canonical_live")
                if not live or local_id not in row_ids:
                    result["missing"] += 1
                    continue
                try: normalize_live_entity(live)
                except (TypeError, ValueError, KeyError) as exc: result["invalid"].append({"run_id": str(cache.run_id), "local_id": local_id, "error": str(exc)})
            if len(row_ids) != len(rows): result["duplicates"].append(str(cache.run_id))
        result["ready"] = bool(result["items"]) and not result["missing"] and not result["invalid"] and not result["duplicates"] and result["items"] == result["canonical_rows"]
        return result

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--run-id", type=uuid.UUID); args = parser.parse_args()
    result = asyncio.run(inspect(args.run_id)); print(json.dumps(result, indent=2, sort_keys=True)); raise SystemExit(0 if result["ready"] else 1)
if __name__ == "__main__": main()
