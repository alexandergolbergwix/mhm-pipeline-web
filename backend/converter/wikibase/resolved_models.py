"""Wikibase entity drafts resolved against live schema PIDs/QIDs.

Sits between :mod:`hmo_exporter` (offline RDF -> local drafts) and the
Phase 5 upload path (real Wikibase writes): every ``ResolvedWikibaseEntity``
here carries only real property/class ids and JSON-safe claim specs — no
network calls, no live entity ids of its own yet (those are assigned at
upload time).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SchemaMappingEntry:
    """One schema-level mapping row, as consumed by the resolver.

    ``datatype`` is ``None`` for classes (a QID has no datatype of its
    own); properties always carry the Wikibase datatype recorded at
    bootstrap time (Phase 3), since that — not the RDF literal's XSD
    type — determines how a statement's value must be shaped.
    """

    wikibase_id: str
    datatype: str | None = None


@dataclass(frozen=True)
class ResolvedClaim:
    """A JSON-safe claim spec: real PID + a value shaped for its datatype.

    Converted into a live ``wikibaseintegrator.datatypes`` object only at
    upload time (Phase 5) — kept as plain data here so it round-trips
    through the Postgres build cache untouched.
    """

    property_id: str
    datatype: str
    value: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "property_id": self.property_id,
            "datatype": self.datatype,
            "value": self.value,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "ResolvedClaim":
        return ResolvedClaim(
            property_id=data["property_id"], datatype=data["datatype"], value=data["value"],
        )


@dataclass(frozen=True)
class DeferredItemLink:
    """An item -> item claim that can't be written until both ends have
    live QIDs — resolved in the upload path's second pass (Phase 5)."""

    source_local_id: str
    property_id: str
    target_local_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_local_id": self.source_local_id,
            "property_id": self.property_id,
            "target_local_id": self.target_local_id,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "DeferredItemLink":
        return DeferredItemLink(
            source_local_id=data["source_local_id"],
            property_id=data["property_id"],
            target_local_id=data["target_local_id"],
        )


@dataclass(frozen=True)
class ResolvedWikibaseEntity:
    """One instance draft resolved to real Wikibase-shaped labels/claims."""

    local_id: str
    labels: dict[str, str]
    descriptions: dict[str, str]
    class_qid: str
    source_uri: str
    entity_type: str = ""
    control_numbers: list[str] = field(default_factory=list)
    authority_evidence: list[dict[str, object]] = field(default_factory=list)
    claims: list[ResolvedClaim] = field(default_factory=list)
    deferred_links: list[DeferredItemLink] = field(default_factory=list)
    # Human-readable notes for statements that couldn't be resolved to a
    # claim (e.g. an object property pointing at a non-instance URI) —
    # surfaced to the curator, never silently dropped.
    skipped_statements: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_id": self.local_id,
            "labels": self.labels,
            "descriptions": self.descriptions,
            "class_qid": self.class_qid,
            "source_uri": self.source_uri,
            "entity_type": self.entity_type,
            "control_numbers": list(self.control_numbers),
            "authority_evidence": list(self.authority_evidence),
            "claims": [c.to_dict() for c in self.claims],
            "deferred_links": [d.to_dict() for d in self.deferred_links],
            "skipped_statements": self.skipped_statements,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "ResolvedWikibaseEntity":
        return ResolvedWikibaseEntity(
            local_id=data["local_id"],
            labels=data["labels"],
            descriptions=data["descriptions"],
            class_qid=data["class_qid"],
            source_uri=data["source_uri"],
            entity_type=str(data.get("entity_type") or ""),
            control_numbers=list(data.get("control_numbers") or []),
            authority_evidence=list(data.get("authority_evidence") or []),
            claims=[ResolvedClaim.from_dict(c) for c in data.get("claims") or []],
            deferred_links=[
                DeferredItemLink.from_dict(d) for d in data.get("deferred_links") or []
            ],
            skipped_statements=list(data.get("skipped_statements") or []),
        )


class UnmappedOntologyUriError(ValueError):
    """Raised when a draft references a class/property URI with no live
    schema mapping — guards against ontology drift (a bootstrap that
    hasn't been re-run since the ontology grew). Fails closed: the whole
    batch is rejected rather than silently dropping the unmapped
    statements/entities."""

    def __init__(self, missing_uris: list[str]) -> None:
        self.missing_uris = missing_uris
        super().__init__(
            f"{len(missing_uris)} ontology URI(s) have no live schema mapping "
            f"— run the schema bootstrap first: {', '.join(missing_uris[:10])}"
            + ("…" if len(missing_uris) > 10 else "")
        )
