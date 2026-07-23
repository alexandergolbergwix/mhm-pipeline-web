"""Read-only production E2E audit for the canonical HMO pipeline.

The command validates live HMO read-back, projects the same canonical state to
RDF and Wikidata Studio, and reports legacy/canonical shadow differences. It
never writes Wikidata and never mutates live HMO items.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db import session_scope
from app.models.hmo_canonical_entity import HmoCanonicalEntity
from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.rdf_artifact import RdfArtifact
from app.models.run import AuthorityMatch
from app.pipeline.hmo_authority_gate import validate_authority_rows
from app.pipeline.rdf_build import build_rdf_from_hmo_canonical_cache
from app.pipeline.projection_shadow_compare import compare_rdf_projections
from app.routers.wikidata_studio import execute_studio_build
from scripts.check_hmo_canonical_gate import inspect as inspect_canonical


def _studio_summary(row: Any) -> dict[str, Any]:
    items = list(row.result_items or [])
    local_ids = {str(item.get("local_id") or "") for item in items}
    return {
        "source": row.source,
        "items": len(items),
        "unique_local_ids": len(local_ids),
        "quickstatements_lines": len([line for line in str(row.quickstatements or "").splitlines() if line.strip()]),
        "fingerprint": row.input_fingerprint,
        "summary": row.summary or {},
    }


def _studio_shadow_diff(legacy: Any, canonical: Any) -> dict[str, Any]:
    """Compare item payloads and QuickStatements, not just row counts."""
    legacy_items = {str(item.get("local_id") or ""): item for item in (legacy.result_items or [])}
    canonical_items = {str(item.get("local_id") or ""): item for item in (canonical.result_items or [])}
    legacy_ids = set(legacy_items)
    canonical_ids = set(canonical_items)
    changed: list[dict[str, Any]] = []
    compared_fields = (
        "source_uri", "hmo_wikibase_id", "labels", "descriptions", "aliases",
        "claims", "wikidata_claims", "authority_evidence", "projection_source",
    )
    for local_id in sorted(legacy_ids & canonical_ids):
        fields = {
            field: {"legacy": legacy_items[local_id].get(field), "canonical": canonical_items[local_id].get(field)}
            for field in compared_fields
            if legacy_items[local_id].get(field) != canonical_items[local_id].get(field)
        }
        if fields:
            changed.append({"local_id": local_id, "fields": fields})

    def lines(value: Any) -> set[str]:
        return {line.strip() for line in str(value or "").splitlines() if line.strip()}

    legacy_qs = lines(legacy.quickstatements)
    canonical_qs = lines(canonical.quickstatements)
    only_legacy_qs = sorted(legacy_qs - canonical_qs)
    only_canonical_qs = sorted(canonical_qs - legacy_qs)
    return {
        "identical": not (legacy_ids - canonical_ids or canonical_ids - legacy_ids or changed or only_legacy_qs or only_canonical_qs),
        "items": {
            "legacy": len(legacy_items),
            "canonical": len(canonical_items),
            "only_legacy": sorted(legacy_ids - canonical_ids),
            "only_canonical": sorted(canonical_ids - legacy_ids),
            "changed_count": len(changed),
            "changed_sample": changed[:50],
        },
        "quickstatements": {
            "legacy": len(legacy_qs),
            "canonical": len(canonical_qs),
            "identical": not only_legacy_qs and not only_canonical_qs,
            "only_legacy": only_legacy_qs,
            "only_canonical": only_canonical_qs,
        },
    }


async def audit(run_id: uuid.UUID) -> dict[str, Any]:
    gate = await inspect_canonical(run_id)
    result: dict[str, Any] = {
        "run_id": str(run_id),
        "canonical_gate": gate,
        "authority_false_positive_gate": None,
        "rdf": None,
        "rdf_shadow": None,
        "wikidata": None,
        "ready": False,
    }

    with tempfile.TemporaryDirectory(prefix="hmo-canonical-e2e-") as tmp:
        tmp_path = Path(tmp)
        legacy_path = tmp_path / "legacy.ttl"
        canonical_path = tmp_path / "canonical.ttl"
        async with session_scope() as db:
            artifact = await db.get(RdfArtifact, run_id)
            if artifact is None:
                result["rdf"] = {"error": "missing legacy RDF artifact"}
            else:
                legacy_path.write_text(artifact.ttl_content, encoding="utf-8")
                try:
                    canonical_build = await build_rdf_from_hmo_canonical_cache(db, run_id, canonical_path)
                    result["rdf"] = {"triples": canonical_build.triples_count}
                    result["rdf_shadow"] = compare_rdf_projections(legacy_path, canonical_path)
                except Exception as exc:  # noqa: BLE001
                    result["rdf"] = {"error": str(exc)}

            matches = (await db.execute(select(AuthorityMatch).where(AuthorityMatch.run_id == run_id))).scalars().all()
            result["authority_false_positive_gate"] = validate_authority_rows(matches)

            try:
                legacy = await execute_studio_build(
                    db, run_id=run_id, approved_only=False, source="legacy",
                    force_rebuild=False, run_user_id=None,
                )
                canonical = await execute_studio_build(
                    db, run_id=run_id, approved_only=False, source="canonical",
                    force_rebuild=True, run_user_id=None,
                )
                result["wikidata"] = {
                    "legacy": _studio_summary(legacy),
                    "canonical": _studio_summary(canonical),
                    "shadow": _studio_shadow_diff(legacy, canonical),
                }
            except Exception as exc:  # noqa: BLE001
                result["wikidata"] = {"error": str(exc)}

            # Keep this query in the audit output: it proves the durable rows
            # used by both projections exist, not just the transient cache.
            durable_rows = (await db.execute(select(HmoCanonicalEntity).where(HmoCanonicalEntity.run_id == run_id))).scalars().all()
            result["durable_canonical_rows"] = len(durable_rows)
            result["hmo_cache_present"] = (await db.scalar(select(HmoStudioItemCache.id).where(HmoStudioItemCache.run_id == run_id))) is not None

    rdf_ok = bool(result.get("rdf", {}).get("triples", 0))
    wikidata = result.get("wikidata") or {}
    wikidata_ok = bool((wikidata.get("canonical") or {}).get("items", 0)) and bool((wikidata.get("canonical") or {}).get("quickstatements_lines", 0)) and bool((wikidata.get("shadow") or {}).get("identical"))
    authority_ok = bool((result.get("authority_false_positive_gate") or {}).get("ready"))
    result["ready"] = bool(gate.get("ready") and rdf_ok and wikidata_ok and authority_ok)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", type=uuid.UUID)
    args = parser.parse_args()
    result = asyncio.run(audit(args.run_id))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
