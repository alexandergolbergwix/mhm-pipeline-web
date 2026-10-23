"""Local rule-verify loop on production data (READ-ONLY, no writes).

Loads the run's merged HMO Studio items from DATABASE_URL and runs the
same rule engine the Modal shards run — in-process. No deploy, no writes;
iterate on checker code locally and only ship when the numbers look right.
API/live rules report "error (disabled)" without --api (throttled probes).

    export DATABASE_URL=$(heroku config:get DATABASE_URL -a mhm-pipeline-web)
    python backend/scripts/rule_verify_local.py                  # full corpus
    python backend/scripts/rule_verify_local.py --ids-file fails.txt
"""
import argparse
import asyncio
import collections
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

if not os.environ.get("DATABASE_URL"):
    sys.exit("set DATABASE_URL first: export DATABASE_URL=$(heroku config:get DATABASE_URL -a mhm-pipeline-web)")

DEFAULT_RUN = "47e03437-09f2-4d6b-aea7-89fe79d76198"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=DEFAULT_RUN)
    ap.add_argument("--ids-file", default=None)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import _ssl_connect_args
    from app.pipeline.hmo_item_views import fetch_merged_hmo_items_cached
    from app.pipeline.rule_verify.base import STATE_FAIL
    from app.pipeline.rule_verify.context import RuleContext
    from app.pipeline.rule_verify.engine import RuleEngine
    from app.pipeline.rule_verify.rules.hmo import build_hmo_rules

    url = os.environ["DATABASE_URL"].replace("postgres://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(
        url, pool_pre_ping=True,
        connect_args=_ssl_connect_args(url),
    )
    session = async_sessionmaker(engine, expire_on_commit=False)
    async with session() as db:
        items = await fetch_merged_hmo_items_cached(db, args.run)
    await engine.dispose()

    wanted = None
    if args.ids_file:
        wanted = {line.strip() for line in open(args.ids_file) if line.strip()}
    if wanted:
        items = [it for it in items if str(it.get("_local_id") or it.get("local_id")) in wanted]
    if args.limit:
        items = items[: args.limit]
    print(f"scope: {len(items)} items")

    rules = build_hmo_rules()
    ctx = RuleContext(run_id=args.run)  # api_enabled=False -> live rules error out
    eng = RuleEngine(rules, ctx)
    per_rule: dict[str, int] = collections.Counter()
    fail_items: dict[str, list[str]] = collections.defaultdict(list)
    for i, item in enumerate(items):
        for res in eng.run_entity(item):
            if res.state in (STATE_FAIL, "error"):
                per_rule[f"{res.rule_id}:{res.state}"] += 1
                if res.state == STATE_FAIL:
                    fail_items[res.rule_id].append(
                        str(item.get("_local_id") or item.get("local_id")))
        if (i + 1) % 2000 == 0:
            print(f"  ...{i + 1}", flush=True)
    print("--- local fail/error per rule ---")
    for key, n in per_rule.most_common():
        print(f"{key}: {n}")
    for rid, ids in fail_items.items():
        name = rid.replace(".", "_")
        with open(f"/tmp/fails_{name}.txt", "w") as f:
            f.write("\n".join(ids))
        print(f"wrote /tmp/fails_{name}.txt ({len(ids)})")


asyncio.run(main())
