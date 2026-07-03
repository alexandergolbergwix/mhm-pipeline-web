"""Offline schema bootstrap drafts for an HMO project Wikibase.

Draft-building is pure and offline: :func:`build_default_hmo_schema_bootstrap`
parses the real HMO ontology (via :mod:`ontology_schema_reader`) into
:class:`WikibaseSchemaClassDraft`/:class:`WikibaseSchemaPropertyDraft`
lists. Turning these drafts into live Wikibase entities on
``mhm-hmo.wikibase.cloud`` is the job of
``backend/app/pipeline/hmo_schema_bootstrap.py`` (Phase 3) — this module
still performs no network calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from converter.wikibase.ontology_schema_reader import (
    OntologyClassEntry,
    OntologyPropertyEntry,
    OntologySchema,
    read_hmo_schema,
)


@dataclass(frozen=True)
class WikibaseSchemaClassDraft:
    """Draft description of one HMO-aligned Wikibase class item."""

    local_id: str
    label: str
    description: str
    source_uri: str
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable representation of the class draft."""
        return {
            "local_id": self.local_id,
            "label": self.label,
            "description": self.description,
            "source_uri": self.source_uri,
            "aliases": self.aliases,
        }


@dataclass(frozen=True)
class WikibaseSchemaPropertyDraft:
    """Draft description of one HMO-aligned Wikibase property."""

    local_id: str
    label: str
    description: str
    datatype: str
    source_uri: str
    aliases: list[str] = field(default_factory=list)
    expected_value: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable representation of the property draft."""
        data: dict[str, object] = {
            "local_id": self.local_id,
            "label": self.label,
            "description": self.description,
            "datatype": self.datatype,
            "source_uri": self.source_uri,
            "aliases": self.aliases,
        }
        if self.expected_value is not None:
            data["expected_value"] = self.expected_value
        return data


@dataclass(frozen=True)
class WikibaseSchemaBootstrap:
    """Export-only HMO Wikibase schema bootstrap package."""

    classes: list[WikibaseSchemaClassDraft]
    properties: list[WikibaseSchemaPropertyDraft]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable bootstrap package."""
        return {
            "classes": [schema_class.to_dict() for schema_class in self.classes],
            "properties": [schema_property.to_dict() for schema_property in self.properties],
            "notes": self.notes,
        }


def build_default_hmo_schema_bootstrap(
    ttl_path: Path | None = None,
) -> WikibaseSchemaBootstrap:
    """Build the offline schema draft for the full HMO ontology.

    Parses every ``owl:Class``/``owl:ObjectProperty``/``owl:DatatypeProperty``
    from the HMO ontology Turtle file (defaulting to
    :func:`default_hmo_ontology_path`) via :func:`read_hmo_schema`, and maps
    each entry into a :class:`WikibaseSchemaClassDraft` or
    :class:`WikibaseSchemaPropertyDraft`. ``owl:AnnotationProperty``
    declarations are intentionally excluded (see ``OntologySchema.skipped``).
    """
    schema = read_hmo_schema(ttl_path)
    return build_bootstrap_from_ontology_schema(schema)


def build_bootstrap_from_ontology_schema(
    schema: OntologySchema,
) -> WikibaseSchemaBootstrap:
    """Map a parsed :class:`OntologySchema` into schema bootstrap drafts."""
    classes = [
        WikibaseSchemaClassDraft(
            local_id=f"ClassDraft_{slug}",
            label=entry.label,
            description=entry.description,
            source_uri=entry.uri,
            aliases=entry.aliases,
        )
        for entry, slug in zip(schema.classes, _unique_slugs(schema.classes), strict=True)
    ]
    properties = [
        WikibaseSchemaPropertyDraft(
            local_id=f"PropertyDraft_{slug}",
            label=entry.label,
            description=entry.description,
            datatype=entry.datatype,
            source_uri=entry.uri,
            aliases=entry.aliases,
        )
        for entry, slug in zip(schema.properties, _unique_slugs(schema.properties), strict=True)
    ]
    notes = [
        "Offline schema bootstrap only: this module performs no network calls "
        "and no Wikibase writes.",
        "Local IDs are stable draft identifiers, not Wikibase Q/P identifiers.",
        f"Derived from the HMO ontology: {len(schema.classes)} classes, "
        f"{len(schema.properties)} properties, {len(schema.skipped)} skipped "
        "(annotation properties).",
    ]
    return WikibaseSchemaBootstrap(classes=classes, properties=properties, notes=notes)


def _unique_slugs(
    entries: list[OntologyClassEntry] | list[OntologyPropertyEntry],
) -> list[str]:
    """De-duplicate ``local_name`` values that collide across namespaces."""
    counts: dict[str, int] = {}
    slugs: list[str] = []
    for entry in entries:
        counts[entry.local_name] = counts.get(entry.local_name, 0) + 1
        count = counts[entry.local_name]
        slugs.append(entry.local_name if count == 1 else f"{entry.local_name}_{count}")
    return slugs


def export_schema_bootstrap_to_file(path: Path) -> Path:
    """Write the default HMO schema bootstrap JSON to a file."""
    bootstrap = build_default_hmo_schema_bootstrap()
    path.write_text(
        json.dumps(bootstrap.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path
