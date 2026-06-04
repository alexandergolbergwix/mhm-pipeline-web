"""Check what date fields exist in MARC records."""
import asyncio
import json
import sys
sys.path.insert(0, ".")

from sqlalchemy import select
from app.db import SessionLocal
from app.models.run import RunRecord, Run


async def main() -> None:
    async with SessionLocal() as db:
        run = (
            await db.execute(select(Run).order_by(Run.created_at.desc()).limit(1))
        ).scalar_one()
        print("Run:", run.name)

        recs = (
            await db.execute(
                select(RunRecord).where(RunRecord.run_id == run.id).limit(5)
            )
        ).scalars().all()

        for r in recs:
            m = dict(r.marc)
            date_keys = {
                k: v for k, v in m.items()
                if any(x in k.lower() for x in ("date", "year", "008", "260", "264", "production"))
            }
            print(f"\nCN: {r.control_number}")
            print("Date fields:", json.dumps(date_keys, ensure_ascii=False))
            print("All keys:", sorted(m.keys()))


asyncio.run(main())
