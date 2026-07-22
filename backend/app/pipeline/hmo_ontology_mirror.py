"""Validate that the HMO Wikibase schema mirrors the ontology vocabulary."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def compare_ontology_mirror(
    ontology_uris: Iterable[str],
    mapped_uris: Iterable[str],
) -> dict[str, Any]:
    expected = {str(uri).strip() for uri in ontology_uris if str(uri).strip()}
    actual = {str(uri).strip() for uri in mapped_uris if str(uri).strip()}
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    return {
        "expected": len(expected),
        "mapped": len(actual),
        "missing": len(missing),
        "extra": len(extra),
        "missing_examples": missing[:25],
        "extra_examples": extra[:25],
        "mirrored": not missing,
    }
