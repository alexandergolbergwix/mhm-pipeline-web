"""Parse the Hebrew Manuscripts Ontology (HMO) into schema entries.

This is the ontology-driven replacement for the hand-written 10/14-item
list that used to live in :mod:`schema_bootstrap`. It reads every
``owl:Class``, ``owl:ObjectProperty``, and ``owl:DatatypeProperty``
declared in ``backend/ontology/hebrew-manuscripts.ttl`` so the Wikibase
schema bootstrap (Phase 3, see ``dev-docs/hmo-wikibase-studio-plan.md``)
mirrors the full ontology rather than a curated subset.

Performs no network calls — this module only parses the local Turtle
file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

from converter.wikibase._ids import local_name

logger = logging.getLogger(__name__)

_SKOS_DEFINITION = URIRef("http://www.w3.org/2004/02/skos/core#definition")

# xsd datatypes that map to Wikibase's "time" datatype.
_TIME_RANGES = frozenset({XSD.date, XSD.dateTime, XSD.gYear, XSD.gYearMonth})
_URL_RANGES = frozenset({XSD.anyURI})


@dataclass(frozen=True)
class OntologyClassEntry:
    """One ``owl:Class`` declaration from the HMO ontology."""

    uri: str
    local_name: str
    label: str
    description: str
    aliases: list[str] = field(default_factory=list)
    parent_uri: str | None = None


@dataclass(frozen=True)
class OntologyPropertyEntry:
    """One ``owl:ObjectProperty``/``owl:DatatypeProperty`` declaration."""

    uri: str
    local_name: str
    label: str
    description: str
    datatype: str
    aliases: list[str] = field(default_factory=list)
    parent_uri: str | None = None


@dataclass(frozen=True)
class OntologySkippedEntry:
    """A schema declaration intentionally excluded from the bootstrap."""

    uri: str
    kind: str
    reason: str


@dataclass(frozen=True)
class OntologySchema:
    """Full parse result of the HMO ontology's schema declarations."""

    classes: list[OntologyClassEntry]
    properties: list[OntologyPropertyEntry]
    skipped: list[OntologySkippedEntry] = field(default_factory=list)


def default_hmo_ontology_path() -> Path:
    """Return the path to the bundled HMO ontology Turtle file."""
    return Path(__file__).resolve().parents[2] / "ontology" / "hebrew-manuscripts.ttl"


def read_hmo_schema(ttl_path: Path | None = None) -> OntologySchema:
    """Parse the HMO ontology Turtle file into class/property entries."""
    graph = Graph()
    graph.parse(ttl_path or default_hmo_ontology_path())

    classes = [
        _build_class_entry(graph, subject) for subject in _subjects_of_type(graph, OWL.Class)
    ]

    skipped = [
        OntologySkippedEntry(
            uri=str(subject),
            kind="AnnotationProperty",
            reason="annotation properties describe metadata, not domain facts",
        )
        for subject in _subjects_of_type(graph, OWL.AnnotationProperty)
    ]

    properties: list[OntologyPropertyEntry] = []
    for kind, is_object in ((OWL.ObjectProperty, True), (OWL.DatatypeProperty, False)):
        for subject in _subjects_of_type(graph, kind):
            properties.append(_build_property_entry(graph, subject, is_object=is_object))

    return OntologySchema(
        classes=sorted(classes, key=lambda entry: entry.uri),
        properties=sorted(properties, key=lambda entry: entry.uri),
        skipped=sorted(skipped, key=lambda entry: entry.uri),
    )


def _subjects_of_type(graph: Graph, class_uri: URIRef) -> list[URIRef]:
    """Return every subject asserted as ``rdf:type class_uri``, sorted."""
    return sorted(
        (
            subject
            for subject in graph.subjects(RDF.type, class_uri)
            if isinstance(subject, URIRef)
        ),
        key=str,
    )


def _build_class_entry(graph: Graph, subject: URIRef) -> OntologyClassEntry:
    name = local_name(subject)
    label, aliases = _label_and_aliases(graph, subject, fallback=name.replace("_", " "))
    description = _description(graph, subject, kind="class", name=name)
    parent = _first_object(graph, subject, RDFS.subClassOf, only_uri=True)
    return OntologyClassEntry(
        uri=str(subject),
        local_name=name,
        label=label,
        description=description,
        aliases=aliases,
        parent_uri=str(parent) if parent is not None else None,
    )


def _build_property_entry(
    graph: Graph, subject: URIRef, *, is_object: bool
) -> OntologyPropertyEntry:
    name = local_name(subject)
    label, aliases = _label_and_aliases(graph, subject, fallback=name.replace("_", " "))
    description = _description(graph, subject, kind="property", name=name)
    parent = _first_object(graph, subject, RDFS.subPropertyOf, only_uri=True)
    range_value = _first_object(graph, subject, RDFS.range, only_uri=True)
    datatype = _infer_datatype(subject, range_value, is_object=is_object)
    return OntologyPropertyEntry(
        uri=str(subject),
        local_name=name,
        label=label,
        description=description,
        datatype=datatype,
        aliases=aliases,
        parent_uri=str(parent) if parent is not None else None,
    )


def _label_and_aliases(
    graph: Graph, subject: URIRef, *, fallback: str
) -> tuple[str, list[str]]:
    """Pick the ``en`` label as the primary label; other languages become aliases."""
    labels: dict[str, str] = {}
    for obj in graph.objects(subject, RDFS.label):
        if isinstance(obj, Literal):
            labels[obj.language or "und"] = str(obj)
    if not labels:
        return fallback, []
    if "en" in labels:
        primary = labels.pop("en")
    else:
        first_lang = sorted(labels)[0]
        primary = labels.pop(first_lang)
    aliases = [value for value in labels.values() if value != primary]
    return primary, aliases


def _description(graph: Graph, subject: URIRef, *, kind: str, name: str) -> str:
    for predicate in (RDFS.comment, _SKOS_DEFINITION):
        for obj in graph.objects(subject, predicate):
            if isinstance(obj, Literal) and str(obj).strip():
                if obj.language in (None, "en"):
                    return str(obj)
        # Fall back to any language for that predicate if no English form exists.
        for obj in graph.objects(subject, predicate):
            if isinstance(obj, Literal) and str(obj).strip():
                return str(obj)
    return f"HMO {kind}: {name}"


def _first_object(
    graph: Graph, subject: URIRef, predicate: URIRef, *, only_uri: bool
) -> URIRef | None:
    for obj in graph.objects(subject, predicate):
        if only_uri and not isinstance(obj, URIRef):
            continue
        if isinstance(obj, URIRef):
            return obj
    return None


def _infer_datatype(subject: URIRef, range_value: URIRef | None, *, is_object: bool) -> str:
    if range_value is None:
        if is_object:
            return "wikibase-item"
        logger.warning("No rdfs:range for datatype property %s; defaulting to string", subject)
        return "string"
    if range_value in _TIME_RANGES:
        return "time"
    if range_value in _URL_RANGES:
        return "url"
    if str(range_value).startswith(str(XSD)):
        return "string"
    # Non-xsd range on an object property (or an untyped datatype property
    # pointing at a class) means the range is itself an HMO/CIDOC/LRMoo class.
    return "wikibase-item"
