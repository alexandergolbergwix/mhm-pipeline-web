"""Compare legacy and canonical projections without mutating either source."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rdflib import Graph


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
