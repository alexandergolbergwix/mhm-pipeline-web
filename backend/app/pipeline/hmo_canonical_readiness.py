from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.pipeline.hmo_canonical import canonical_entity_fingerprint, normalize_live_entity


@dataclass
class CanonicalReadiness:
    expected_item_count: int
    durable_row_count: int
    missing_rows: list[str] = field(default_factory=list)
    malformed_rows: list[dict[str, Any]] = field(default_factory=list)
    duplicate_local_ids: list[str] = field(default_factory=list)
    duplicate_source_uris: list[str] = field(default_factory=list)
    duplicate_wikibase_qids: list[str] = field(default_factory=list)
    authority_conflicts: list[dict[str, Any]] = field(default_factory=list)
    stale_fingerprints: list[str] = field(default_factory=list)
    missing_fingerprints: list[str] = field(default_factory=list)
    live_readback_failures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return (
            self.expected_item_count > 0
            and self.expected_item_count == self.durable_row_count
            and not self.missing_rows
            and not self.malformed_rows
            and not self.duplicate_local_ids
            and not self.duplicate_source_uris
            and not self.duplicate_wikibase_qids
            and not self.authority_conflicts
            and not self.stale_fingerprints
            and not self.missing_fingerprints
            and not self.live_readback_failures
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_item_count": self.expected_item_count,
            "durable_row_count": self.durable_row_count,
            "missing_rows": self.missing_rows,
            "malformed_rows": self.malformed_rows,
            "duplicate_local_ids": self.duplicate_local_ids,
            "duplicate_source_uris": self.duplicate_source_uris,
            "duplicate_wikibase_qids": self.duplicate_wikibase_qids,
            "authority_conflicts": self.authority_conflicts,
            "stale_fingerprints": self.stale_fingerprints,
            "missing_fingerprints": self.missing_fingerprints,
            "live_readback_failures": self.live_readback_failures,
            "ready": self.ready,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CanonicalReadiness":
        if "results" in value:
            results = [cls.from_dict(result) for result in value.get("results") or []]
            combined = cls(
                expected_item_count=sum(result.expected_item_count for result in results),
                durable_row_count=sum(result.durable_row_count for result in results),
            )
            for field in (
                "missing_rows", "malformed_rows", "duplicate_local_ids",
                "duplicate_source_uris", "duplicate_wikibase_qids",
                "authority_conflicts", "stale_fingerprints", "missing_fingerprints",
                "live_readback_failures",
            ):
                setattr(combined, field, [item for result in results for item in getattr(result, field)])
            return combined
        fields = {
            "expected_item_count", "durable_row_count", "missing_rows",
            "malformed_rows", "duplicate_local_ids", "duplicate_source_uris",
            "duplicate_wikibase_qids", "authority_conflicts", "stale_fingerprints",
            "missing_fingerprints", "live_readback_failures",
        }
        return cls(**{field: value.get(field, 0 if field.endswith("_count") else []) for field in fields})


def _value(item: Any, field: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(field, default)
    return getattr(item, field, default)


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def evaluate(
    expected_items: list[dict[str, Any]],
    canonical_rows: list[Any],
    *,
    authority_conflicts: list[dict[str, Any]] | None = None,
    live_readback_failures: list[dict[str, Any]] | None = None,
) -> CanonicalReadiness:
    """Evaluate durable rows against the current HMO build contract."""
    expected_by_local_id = {
        str(item.get("local_id") or ""): item for item in expected_items
    }
    row_local_ids = [str(_value(row, "local_id", "") or "") for row in canonical_rows]
    row_source_uris = [str(_value(row, "source_uri", "") or "") for row in canonical_rows]
    row_qids = [str(_value(row, "wikibase_id", "") or "") for row in canonical_rows]
    readiness = CanonicalReadiness(
        expected_item_count=len(expected_items),
        durable_row_count=len(canonical_rows),
        duplicate_local_ids=_duplicates([value for value in row_local_ids if value]),
        duplicate_source_uris=_duplicates([value for value in row_source_uris if value]),
        duplicate_wikibase_qids=_duplicates([value for value in row_qids if value]),
        authority_conflicts=list(authority_conflicts or []),
        live_readback_failures=list(live_readback_failures or []),
    )
    rows_by_local_id: dict[str, list[Any]] = {}
    for row in canonical_rows:
        rows_by_local_id.setdefault(str(_value(row, "local_id", "") or ""), []).append(row)

    for local_id, expected in expected_by_local_id.items():
        matches = rows_by_local_id.get(local_id, [])
        live = expected.get("canonical_live")
        if not live or not matches:
            readiness.missing_rows.append(local_id)
            continue
        expected_fingerprint = str(live.get("source_fingerprint") or "").strip()
        for row in matches:
            snapshot = _value(row, "snapshot", {}) or {}
            try:
                normalized = normalize_live_entity(dict(snapshot))
            except (TypeError, ValueError, KeyError) as exc:
                readiness.malformed_rows.append({"local_id": local_id, "error": str(exc)})
                continue
            stored_fingerprint = str(_value(row, "source_fingerprint", "") or "").strip()
            if not stored_fingerprint:
                readiness.missing_fingerprints.append(local_id)
            elif stored_fingerprint != canonical_entity_fingerprint(normalized.to_dict()):
                readiness.stale_fingerprints.append(local_id)
            if expected_fingerprint and stored_fingerprint != expected_fingerprint:
                readiness.stale_fingerprints.append(local_id)

    expected_ids = set(expected_by_local_id)
    readiness.missing_rows.extend(
        local_id for local_id in row_local_ids if local_id and local_id not in expected_ids
    )
    readiness.missing_rows = sorted(set(readiness.missing_rows))
    readiness.stale_fingerprints = sorted(set(readiness.stale_fingerprints))
    readiness.missing_fingerprints = sorted(set(readiness.missing_fingerprints))
    return readiness


def check(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Compatibility wrapper for cache-only callers."""
    local_ids = [str(item.get("local_id") or "") for item in items]
    source_uris = [str(item.get("source_uri") or "") for item in items]
    qids = [str(item.get("wikibase_id") or "") for item in items]
    missing = [item.get("local_id") for item in items if not item.get("canonical_live")]
    return {
        "items": len(items),
        "missing_canonical_live": len(missing),
        "missing_examples": missing[:20],
        "duplicate_local_ids": len(local_ids) - len(set(local_ids)),
        "duplicate_source_uris": len(source_uris) - len(set(source_uris)),
        "duplicate_wikibase_ids": len([q for q in qids if q]) - len({q for q in qids if q}),
        "ready": bool(items)
        and not missing
        and len(local_ids) == len(set(local_ids))
        and len(source_uris) == len(set(source_uris)),
    }
