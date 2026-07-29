"""Canonical HMO entity read model and projection boundary.

HMO Wikibase is the authoritative entity store.  This module keeps the
projection code independent from the live Wikibase client: upload/read-back
code supplies a normalized snapshot, while RDF and Wikidata consumers read
only this shape.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable
from collections.abc import Mapping


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
    entity_type: str = ""
    control_numbers: list[str] = field(default_factory=list)

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
            "entity_type": self.entity_type,
            "control_numbers": list(self.control_numbers),
        }


_FINGERPRINT_SCALARS = ("local_id", "source_uri", "wikibase_id")
_FINGERPRINT_MAPPINGS = ("labels", "descriptions", "aliases")
_FINGERPRINT_SEQUENCES = ("claims", "authority_evidence", "control_numbers")


def canonical_entity_fingerprint(entity: dict[str, Any]) -> str:
    """Hash the normalized live-item state, excluding volatile timestamps.

    An absent container and an empty one are the same state, so they must hash
    the same — otherwise fingerprinting a raw read-back snapshot and then
    re-fingerprinting it after ``normalize_live_entity`` disagree, and the
    readiness gate reports a stale fingerprint for an unchanged item.
    """
    stable: dict[str, Any] = {
        key: str(entity.get(key) or "") for key in _FINGERPRINT_SCALARS
    }
    stable.update({key: dict(entity.get(key) or {}) for key in _FINGERPRINT_MAPPINGS})
    stable.update({key: list(entity.get(key) or []) for key in _FINGERPRINT_SEQUENCES})
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
        entity_type=str(normalized.get("entity_type") or ""),
        control_numbers=[str(cn) for cn in (raw.get("control_numbers") or []) if str(cn).strip()],
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


def _language_values(raw: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(raw, Mapping):
        return out
    for language, value in raw.items():
        if isinstance(value, Mapping):
            text = value.get("value")
        else:
            text = value
        if text is not None and str(text).strip():
            out[str(language)] = str(text)
    return out


def _alias_values(raw: Any) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if not isinstance(raw, Mapping):
        return out
    for language, values in raw.items():
        if not isinstance(values, list):
            values = [values]
        normalized: list[str] = []
        for value in values:
            text = value.get("value") if isinstance(value, Mapping) else value
            if text is not None and str(text).strip():
                normalized.append(str(text))
        if normalized:
            out[str(language)] = normalized
    return out


def _wikibase_value(raw: Any) -> tuple[Any, str | None]:
    if not isinstance(raw, Mapping):
        return raw, None
    data_type = str(raw.get("type") or "")
    if data_type == "wikibase-entityid":
        value = raw.get("value") if isinstance(raw.get("value"), Mapping) else raw
        qid = str(value.get("id") or "").strip()
        if not qid and value.get("numeric-id") is not None:
            qid = "Q" + str(value["numeric-id"])
        return qid, "wikibase-item"
    if data_type == "monolingualtext":
        return {"text": str(raw.get("text") or ""), "language": str(raw.get("language") or "")}, "monolingualtext"
    if data_type == "time":
        return dict(raw), "time"
    if data_type == "quantity":
        return dict(raw), "quantity"
    return raw.get("value", raw), None


def canonical_snapshot_from_wikibase(
    raw: Mapping[str, Any],
    *,
    local_id: str,
    source_uri: str,
    authority_evidence: list[dict[str, Any]],
    entity_type: str,
    control_numbers: list[str],
    property_uris: Mapping[str, str],
    target_uris: Mapping[str, str],
) -> dict[str, Any]:
    """Convert raw WikibaseIntegrator JSON to the canonical HMO contract."""
    claims: list[dict[str, Any]] = []
    raw_claims = raw.get("claims") or {}
    if isinstance(raw_claims, Mapping):
        for property_id, statements in raw_claims.items():
            property_id = str(property_id)
            property_uri = str(property_uris.get(property_id) or "").strip()
            if not property_uri:
                continue
            if not isinstance(statements, list):
                statements = [statements]
            for statement in statements:
                if not isinstance(statement, Mapping):
                    continue
                snak = statement.get("mainsnak")
                if not isinstance(snak, Mapping) or snak.get("snaktype") != "value":
                    continue
                value, datatype = _wikibase_value(snak.get("datavalue"))
                if datatype == "wikibase-item":
                    target_uri = str(target_uris.get(str(value)) or "").strip()
                    if target_uri:
                        claims.append({"property_uri": property_uri, "target_uri": target_uri, "property_id": property_id})
                    continue
                claims.append({"property_uri": property_uri, "property_id": property_id, "datatype": datatype or str(snak.get("datatype") or "string"), "value": value})
    snapshot = {
        "local_id": local_id,
        "source_uri": source_uri,
        "wikibase_id": str(raw.get("id") or ""),
        "labels": _language_values(raw.get("labels")),
        "descriptions": _language_values(raw.get("descriptions")),
        "aliases": _alias_values(raw.get("aliases")),
        "claims": claims,
        "authority_evidence": list(authority_evidence),
        "entity_type": entity_type,
        "control_numbers": list(control_numbers),
        "canonical_source": "wikibase",
    }
    snapshot["source_fingerprint"] = canonical_entity_fingerprint(snapshot)
    return snapshot
