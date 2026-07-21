"""Canonical HMO entity read model and projection boundary.

HMO Wikibase is the authoritative entity store.  This module keeps the
projection code independent from the live Wikibase client: upload/read-back
code supplies a normalized snapshot, while RDF and Wikidata consumers read
only this shape.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class CanonicalHmoEntity:
    local_id: str
    source_uri: str
    wikibase_id: str
    revision_id: int | None
    labels: dict[str, str]
    descriptions: dict[str, str]
    aliases: dict[str, list[str]]
    claims: list[dict[str, Any]]
    authority_evidence: list[dict[str, Any]]
    source_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_id": self.local_id,
            "source_uri": self.source_uri,
            "wikibase_id": self.wikibase_id,
            "revision_id": self.revision_id,
            "labels": self.labels,
            "descriptions": self.descriptions,
            "aliases": self.aliases,
            "claims": self.claims,
            "authority_evidence": self.authority_evidence,
            "source_fingerprint": self.source_fingerprint,
        }


def canonical_entity_fingerprint(entity: dict[str, Any]) -> str:
    """Hash the normalized live-item state, excluding volatile timestamps."""
    stable = {
        key: entity.get(key)
        for key in (
            "local_id", "source_uri", "wikibase_id", "labels", "descriptions",
            "aliases", "claims", "authority_evidence",
        )
    }
    return hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def normalize_live_entity(raw: dict[str, Any]) -> CanonicalHmoEntity:
    """Normalize a read-back Wikibase item into the canonical contract."""
    required = ("local_id", "source_uri", "wikibase_id")
    missing = [key for key in required if not str(raw.get(key) or "").strip()]
    if missing:
        raise ValueError(f"canonical HMO entity missing required fields: {', '.join(missing)}")
    normalized = dict(raw)
    normalized["labels"] = dict(raw.get("labels") or {})
    normalized["descriptions"] = dict(raw.get("descriptions") or {})
    normalized["aliases"] = {
        str(language): [str(value) for value in values]
        for language, values in (raw.get("aliases") or {}).items()
    }
    normalized["claims"] = list(raw.get("claims") or [])
    normalized["authority_evidence"] = list(raw.get("authority_evidence") or [])
    normalized["source_fingerprint"] = canonical_entity_fingerprint(normalized)
    return CanonicalHmoEntity(
        local_id=str(normalized["local_id"]),
        source_uri=str(normalized["source_uri"]),
        wikibase_id=str(normalized["wikibase_id"]),
        revision_id=normalized.get("revision_id"),
        labels=normalized["labels"],
        descriptions=normalized["descriptions"],
        aliases=normalized["aliases"],
        claims=normalized["claims"],
        authority_evidence=normalized["authority_evidence"],
        source_fingerprint=normalized["source_fingerprint"],
    )


def assert_canonical_entities(entities: Iterable[CanonicalHmoEntity]) -> None:
    """Reject duplicate local IDs, source URIs, or Wikibase IDs."""
    seen: dict[str, set[str]] = {"local_id": set(), "source_uri": set(), "wikibase_id": set()}
    for entity in entities:
        for field in seen:
            value = getattr(entity, field)
            if value in seen[field]:
                raise ValueError(f"duplicate canonical HMO {field}: {value}")
            seen[field].add(value)
