"""Validate that the HMO Wikibase schema mirrors the ontology vocabulary."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def compare_ontology_mirror(
    ontology_uris: Iterable[str],
    mapped_uris: Iterable[str],
    *,
    expected_metadata: dict[str, dict[str, str | None]] | None = None,
    mapped_metadata: dict[str, dict[str, str | None]] | None = None,
) -> dict[str, Any]:
    expected = {str(uri).strip() for uri in ontology_uris if str(uri).strip()}
    actual = {str(uri).strip() for uri in mapped_uris if str(uri).strip()}
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    label_drift: list[str] = []
    datatype_drift: list[str] = []
    if expected_metadata is not None and mapped_metadata is not None:
        for uri in sorted(expected & actual):
            expected_row = expected_metadata.get(uri, {})
            mapped_row = mapped_metadata.get(uri, {})
            if expected_row.get("label") and expected_row.get("label") != mapped_row.get("label"):
                label_drift.append(uri)
            expected_datatype = expected_row.get("datatype")
            if expected_datatype and expected_datatype != mapped_row.get("datatype"):
                datatype_drift.append(uri)
    return {
        "expected": len(expected),
        "mapped": len(actual),
        "missing": len(missing),
        "extra": len(extra),
        "missing_examples": missing[:25],
        "extra_examples": extra[:25],
        "label_drift": len(label_drift),
        "label_drift_examples": label_drift[:25],
        "datatype_drift": len(datatype_drift),
        "datatype_drift_examples": datatype_drift[:25],
        "drifted": bool(label_drift or datatype_drift),
        "mirrored": not missing,
    }
