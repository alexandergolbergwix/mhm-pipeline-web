#!/usr/bin/env python3
"""Migrate live Wikibase ``hmo_source_uri`` values to a new namespace.

Rule W-55 moved the HMO ontology + instance namespace from the placeholder
``http://www.ontology.org.il/HebrewManuscripts/2025-12-06#`` to the project's
real w3id permalink ``https://w3id.org/mhm/ontology#``. Items already on
``mhm-hmo.wikibase.cloud`` still carry the OLD ``hmo_source_uri`` value, so
reconcile-by-source-URI (Rule W-30/W-42) would no longer match them → a
re-upload would create duplicates. This one-time migration rewrites the
``hmo_source_uri`` claim on every affected live item from the old prefix to the
new one (a deterministic prefix swap), after which reconciliation works
normally and runs can be safely re-uploaded.

**Dry-run by default** — it queries the wiki (read-only) and prints exactly
which items/values would change. Pass ``--apply`` to perform the live writes.

Environment
-----------
- ``DATABASE_URL`` — Postgres (to resolve the ``hmo_source_uri`` property id).
- ``WIKIBASE_CLOUD_OAUTH_*`` — server OAuth (only needed with ``--apply``).
- ``WIKIBASE_SPARQL_URL`` — SPARQL endpoint; defaults to
  ``https://mhm-hmo.wikibase.cloud/query/sparql``.
- A repo-root ``.env`` is loaded when present.

Examples
--------
Dry-run (safe; shows the plan)::

    cd backend
    DATABASE_URL=... python -m scripts.migrate_hmo_source_uri_namespace

Apply the migration for real::

    DATABASE_URL=... WIKIBASE_CLOUD_OAUTH_ACCESS_TOKEN=... \
        python -m scripts.migrate_hmo_source_uri_namespace --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

DEFAULT_OLD = "http://www.ontology.org.il/HebrewManuscripts/2025-12-06#"
DEFAULT_NEW = "https://w3id.org/mhm/ontology#"
DEFAULT_SPARQL = "https://mhm-hmo.wikibase.cloud/query/sparql"


def _load_dotenv() -> None:
    env_path = _REPO / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        # Strip an aligned inline comment (`false   # note`) — unquoted values
        # in this .env carry them and they break typed Settings parsing.
        if value and not value[0] in "\"'" and " #" in value:
            value = value.split(" #", 1)[0].strip()
        os.environ.setdefault(key.strip(), value.strip("'\""))


async def _resolve_pid() -> str:
    """Resolve the hmo_source_uri property id, namespace-agnostically.

    The schema mapping row may still be keyed by the OLD ontology_uri (before
    the DB mapping migration runs), so match on the local name rather than the
    exact (now-changed) ``HMO_SOURCE_URI`` constant.
    """
    from sqlalchemy import text  # noqa: PLC0415

    from app.db import session_scope  # noqa: PLC0415

    async with session_scope() as db:
        row = (
            await db.execute(
                text(
                    "SELECT wikibase_id FROM wikibase_entity_mappings "
                    "WHERE run_id IS NULL AND ontology_uri LIKE '%hmo_source_uri' "
                    "LIMIT 1"
                )
            )
        ).scalar_one_or_none()
    if not row:
        raise SystemExit(
            "hmo_source_uri property is not mapped — run the HMO schema bootstrap "
            "first, or check DATABASE_URL points at the right DB."
        )
    return row


async def _migrate_db_mappings(old_prefix: str, new_prefix: str, *, apply: bool) -> int:
    """Prefix-swap ``wikibase_entity_mappings.ontology_uri`` old→new (prod DB).

    Covers both schema (class/property) and instance (source_uri) mappings; the
    deployed code resolves NEW-namespace URIs, so without this every ontology
    lookup misses and HMO build/upload breaks.
    """
    from sqlalchemy import text  # noqa: PLC0415

    from app.db import session_scope  # noqa: PLC0415

    async with session_scope() as db:
        n = (
            await db.execute(
                text(
                    "SELECT count(*) FROM wikibase_entity_mappings "
                    "WHERE ontology_uri LIKE :pat"
                ),
                {"pat": old_prefix + "%"},
            )
        ).scalar_one()
        print(f"[db] wikibase_entity_mappings with old namespace: {n}")
        if not apply or not n:
            return int(n)
        await db.execute(
            text(
                "UPDATE wikibase_entity_mappings "
                "SET ontology_uri = :new || substr(ontology_uri, :cut) "
                "WHERE ontology_uri LIKE :pat"
            ),
            {"new": new_prefix, "cut": len(old_prefix) + 1, "pat": old_prefix + "%"},
        )
        await db.commit()
        remaining = (
            await db.execute(
                text(
                    "SELECT count(*) FROM wikibase_entity_mappings "
                    "WHERE ontology_uri LIKE :pat"
                ),
                {"pat": old_prefix + "%"},
            )
        ).scalar_one()
        print(f"[db] migrated {n} rows; remaining old-namespace: {remaining}")
        return int(n)


async def _fetch_affected(
    sparql_url: str, pid: str, old_prefix: str,
) -> list[tuple[str, str]]:
    """Return ``[(qid, old_value)]`` for live items whose hmo_source_uri
    starts with ``old_prefix`` (read-only)."""
    from app.routers.linked_data_explorer import run_wikibase_sparql  # noqa: PLC0415

    pid_num = pid[1:] if pid.startswith("P") else pid
    # Use the instance's OWN direct-property URI, not the ``wdt:`` prefix — on
    # wikibase.cloud ``wdt:`` defaults to Wikidata's namespace, so ``wdt:P293``
    # matches nothing (the same latent bug lives in hmo_item_reconcile.py).
    base = sparql_url.split("/query/sparql")[0].rstrip("/")
    direct = f"<{base}/prop/direct/P{pid_num}>"
    esc = old_prefix.replace("\\", "\\\\").replace('"', '\\"')
    query = (
        f'SELECT ?item ?val WHERE {{ ?item {direct} ?val . '
        f'FILTER(STRSTARTS(STR(?val), "{esc}")) }}'
    )
    data = await run_wikibase_sparql(sparql_url, query)
    out: list[tuple[str, str]] = []
    for b in data.get("results", {}).get("bindings", []):
        item_uri = b.get("item", {}).get("value", "")
        val = b.get("val", {}).get("value", "")
        qid = item_uri.rsplit("/", 1)[-1]
        if qid.startswith("Q") and val:
            out.append((qid, val))
    return out


def _apply_migration(
    plan: list[dict[str, str]], pid: str, *, sleep_s: float,
) -> list[dict[str, Any]]:
    """Live-write: REPLACE_ALL the hmo_source_uri URL claim per item."""
    from wikibaseintegrator.datatypes import URL  # noqa: PLC0415
    from wikibaseintegrator.wbi_enums import ActionIfExists  # noqa: PLC0415

    from app.services.wikibase_credentials import build_server_wikibase_writer  # noqa: PLC0415
    from converter.wikibase.cloud_client import wikibase_edit_summary  # noqa: PLC0415

    writer = build_server_wikibase_writer()  # ensures_authenticated + verifies user
    writer._init_wbi()  # noqa: SLF001 - maintenance script; reuse the authed wbi
    summary = wikibase_edit_summary("migrate hmo_source_uri to w3id namespace (Rule W-55)")

    results: list[dict[str, Any]] = []
    for i, row in enumerate(plan, 1):
        qid, new = row["qid"], row["new"]
        try:
            entity = writer._get_wbi_entity(qid)  # noqa: SLF001
            entity.claims.add(
                URL(prop_nr=pid, value=new),
                action_if_exists=ActionIfExists.REPLACE_ALL,
            )
            written = entity.write(summary=summary)
            results.append({**row, "status": "updated", "written_id": written.id})
            print(f"  [{i}/{len(plan)}] {qid} -> updated")
        except Exception as exc:  # noqa: BLE001 - record, continue
            results.append({**row, "status": "failed", "message": str(exc)[:300]})
            print(f"  [{i}/{len(plan)}] {qid} -> FAILED: {str(exc)[:160]}")
        if sleep_s:
            time.sleep(sleep_s)
    return results


async def _async_main(args: argparse.Namespace) -> int:
    _load_dotenv()
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is required.")
    sparql_url = args.sparql_url or os.environ.get("WIKIBASE_SPARQL_URL") or DEFAULT_SPARQL

    print(f"old prefix: {args.old_prefix}")
    print(f"new prefix: {args.new_prefix}")

    # ── Part A: prod DB schema/instance mapping table ────────────────────
    if not args.skip_db:
        print("\n--- DB mappings (wikibase_entity_mappings.ontology_uri) ---")
        await _migrate_db_mappings(args.old_prefix, args.new_prefix, apply=args.apply)

    if args.skip_wiki:
        if not args.apply:
            print("\nDRY-RUN. Re-run with --apply to perform writes.")
        return 0

    # ── Part B: live wiki hmo_source_uri claim values ────────────────────
    print("\n--- Live wiki hmo_source_uri claims ---")
    pid = await _resolve_pid()
    print(f"hmo_source_uri property: P{pid.lstrip('P')}")
    print(f"SPARQL endpoint: {sparql_url}")

    affected = await _fetch_affected(sparql_url, pid, args.old_prefix)
    if args.limit is not None:
        affected = affected[: args.limit]
    plan = [
        {"qid": qid, "old": val, "new": val.replace(args.old_prefix, args.new_prefix, 1)}
        for qid, val in affected
        if val.startswith(args.old_prefix)
    ]

    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    plan_path = out_dir / f"source_uri_migration_plan_{stamp}.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\naffected items: {len(plan)}")
    for row in plan[:20]:
        print(f"  {row['qid']}: {row['old']}")
        print(f"          -> {row['new']}")
    if len(plan) > 20:
        print(f"  … and {len(plan) - 20} more (see {plan_path})")
    print(f"\nplan written: {plan_path}")

    if not plan:
        print("\nNothing to migrate — no live items carry the old namespace.")
        return 0

    if not args.apply:
        print("\nDRY-RUN. Re-run with --apply to perform the live writes.")
        return 0

    print(f"\n=== APPLYING {len(plan)} live edits to {sparql_url.split('/query')[0]} ===")
    results = _apply_migration(plan, pid, sleep_s=args.sleep)
    res_path = out_dir / f"source_uri_migration_results_{stamp}.json"
    res_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in results if r["status"] == "updated")
    print(f"\nupdated {ok}/{len(results)}; results -> {res_path}")
    return 0 if ok == len(results) else 1


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--old-prefix", dest="old_prefix", default=DEFAULT_OLD)
    p.add_argument("--new-prefix", dest="new_prefix", default=DEFAULT_NEW)
    p.add_argument("--sparql-url", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--apply", action="store_true", help="perform live writes (default: dry-run)")
    p.add_argument("--skip-db", action="store_true", help="skip the prod DB mapping migration")
    p.add_argument("--skip-wiki", action="store_true", help="skip the live wiki claim migration")
    p.add_argument("--sleep", type=float, default=0.3, help="seconds between live edits")
    p.add_argument("--output-dir", default=str(_REPO / "state" / "source-uri-migration"))
    args = p.parse_args()
    raise SystemExit(asyncio.run(_async_main(args)))


if __name__ == "__main__":
    main()
