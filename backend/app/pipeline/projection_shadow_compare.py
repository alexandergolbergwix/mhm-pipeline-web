"""Compare legacy and canonical projections without mutating either source."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from rdflib import Graph


_ITEM_FIELDS = (
    "source_uri", "hmo_wikibase_id", "labels", "descriptions", "aliases",
    "claims", "wikidata_claims", "authority_evidence", "projection_source",
)


def _item_key(item: dict[str, Any]) -> str:
    return str(item.get("local_id") or item.get("source_uri") or "").strip()


def compare_item_projections(
    legacy_items: Iterable[dict[str, Any]],
    canonical_items: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Compare item projections and classify every observable difference."""
    legacy = {_item_key(item): item for item in legacy_items if _item_key(item)}
    canonical = {_item_key(item): item for item in canonical_items if _item_key(item)}
    differences: list[dict[str, Any]] = []

    for local_id in sorted(legacy.keys() - canonical.keys()):
        differences.append({"local_id": local_id, "kind": "missing_canonical_data", "side": "canonical"})
    for local_id in sorted(canonical.keys() - legacy.keys()):
        differences.append({"local_id": local_id, "kind": "canonical_improvement", "side": "canonical"})

    for local_id in sorted(legacy.keys() & canonical.keys()):
        old = legacy[local_id]
        new = canonical[local_id]
        for field in _ITEM_FIELDS:
            if old.get(field) == new.get(field):
                continue
            if field in {"source_uri", "hmo_wikibase_id"}:
                kind = "identifier_conflict"
            elif field in {"claims", "wikidata_claims"}:
                kind = "claim_conflict"
            elif field in {"labels", "descriptions", "aliases"}:
                kind = "label_description_conflict"
            elif field == "authority_evidence":
                kind = "authority_conflict"
            else:
                kind = "unclassified"
            differences.append({
                "local_id": local_id,
                "field": field,
                "kind": kind,
                "legacy": old.get(field),
                "canonical": new.get(field),
            })

    by_kind: dict[str, int] = {}
    for difference in differences:
        kind = str(difference["kind"])
        by_kind[kind] = by_kind.get(kind, 0) + 1
    blocking_kinds = {"missing_canonical_data", "identifier_conflict", "claim_conflict", "authority_conflict", "unclassified"}
    return {
        "identical": not differences,
        "differences": differences,
        "counts_by_kind": by_kind,
        "blocking_differences": sum(by_kind.get(kind, 0) for kind in blocking_kinds),
        "unclassified_differences": by_kind.get("unclassified", 0),
        "promotion_ready": not differences or not any(kind in blocking_kinds for kind in by_kind),
    }


def compare_rdf_projections(legacy_path: Path, canonical_path: Path) -> dict[str, Any]:
    legacy = Graph().parse(legacy_path, format="turtle")
    canonical = Graph().parse(canonical_path, format="turtle")
    legacy_triples = set(legacy)
    canonical_triples = set(canonical)
    only_legacy = legacy_triples - canonical_triples
    only_canonical = canonical_triples - legacy_triples
    return {
        "legacy_triples": len(legacy_triples),
        "canonical_triples": len(canonical_triples),
        "only_legacy": len(only_legacy),
        "only_canonical": len(only_canonical),
        "identical": not only_legacy and not only_canonical,
        "legacy_sample": [tuple(map(str, triple)) for triple in sorted(only_legacy, key=str)[:20]],
        "canonical_sample": [tuple(map(str, triple)) for triple in sorted(only_canonical, key=str)[:20]],
    }
