"""Export full HMO RDF graphs to offline project-Wikibase draft entities."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS
from rdflib.term import Node

from converter.config.namespaces import CIDOC, HM, LRMOO
from converter.wikibase._ids import local_name, safe_local_id
from converter.wikibase.label_sanitize import sanitize_monolingual_map
from converter.wikibase.models import (
    StatementValue,
    WikibaseEntityDraft,
    WikibaseStatementDraft,
)
from converter.wikibase.resolved_models import (
    DeferredItemLink,
    ResolvedClaim,
    ResolvedWikibaseEntity,
    SchemaMappingEntry,
    UnmappedOntologyUriError,
)

SKIPPED_SCHEMA_TYPES: frozenset[URIRef] = frozenset(
    {
        OWL.Class,
        OWL.ObjectProperty,
        OWL.DatatypeProperty,
        OWL.AnnotationProperty,
        RDF.Property,
    }
)

EXPORT_SKIP_INSTANCE_TYPES: frozenset[URIRef] = frozenset(
    {
        HM.ParadigmBridge,
    }
)

HMO_SOURCE_URI = str(HM.hmo_source_uri)

PREFERRED_CLASS_ORDER: tuple[URIRef, ...] = (
    LRMOO.F4_Manifestation_Singleton,
    LRMOO.F1_Work,
    LRMOO.F2_Expression,
    HM.Codicological_Unit,
    HM.TransmissionWitness,
    HM.TextTradition,
    HM.ParadigmBridge,
    HM.PhilologicalView,
    CIDOC.E21_Person,
    CIDOC.E53_Place,
    CIDOC.E74_Group,
    CIDOC.E12_Production,
    CIDOC.E8_Acquisition,
    HM.DigitalAccess,
)


class HmoWikibaseExporter:
    """Build offline Wikibase-ready drafts from canonical HMO Turtle output."""

    def from_ttl(self, ttl_path: Path) -> list[WikibaseEntityDraft]:
        """Parse a Turtle file and return local Wikibase entity drafts."""
        graph = Graph()
        graph.parse(ttl_path)
        return self.from_graph(graph)

    def from_graph(self, graph: Graph) -> list[WikibaseEntityDraft]:
        """Convert typed RDF nodes into full scholarly Wikibase drafts."""
        typed_nodes = _typed_instance_nodes(graph)
        local_ids = _local_ids_for_nodes(typed_nodes)

        drafts: list[WikibaseEntityDraft] = []
        for subject in typed_nodes:
            class_uri = _preferred_class_uri(graph, subject)
            statements = [
                _statement_from_triple(predicate, obj, local_ids)
                for predicate, obj in sorted(
                    graph.predicate_objects(subject),
                    key=lambda pair: (str(pair[0]), str(pair[1])),
                )
                if predicate not in {RDF.type, RDFS.label}
            ]
            drafts.append(
                WikibaseEntityDraft(
                    local_id=local_ids[subject],
                    labels=_labels_for_node(graph, subject),
                    descriptions=_descriptions_for_node(graph, subject, class_uri),
                    entity_type=local_name(class_uri),
                    class_uri=str(class_uri),
                    source_uri=str(subject),
                    statements=statements,
                )
            )

        return sorted(drafts, key=lambda draft: draft.local_id)

    def export_json(self, entities: list[WikibaseEntityDraft]) -> str:
        """Serialise entity drafts as deterministic UTF-8 JSON text."""
        data = [entity.to_dict() for entity in entities]
        return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)

    def export_json_to_file(
        self,
        entities: list[WikibaseEntityDraft],
        output_path: Path,
    ) -> Path:
        """Write entity drafts to a JSON file and return the output path."""
        output_path.write_text(self.export_json(entities), encoding="utf-8")
        return output_path


def _typed_instance_nodes(graph: Graph) -> list[URIRef | BNode]:
    """Return typed data nodes while skipping ontology/schema declarations."""
    nodes: set[URIRef | BNode] = set()
    for subject, class_uri in graph.subject_objects(RDF.type):
        if not isinstance(subject, URIRef | BNode):
            continue
        if not isinstance(class_uri, URIRef):
            continue
        if class_uri in SKIPPED_SCHEMA_TYPES:
            continue
        if class_uri in EXPORT_SKIP_INSTANCE_TYPES:
            continue
        nodes.add(subject)
    return sorted(nodes, key=str)


def _local_ids_for_nodes(nodes: list[URIRef | BNode]) -> dict[URIRef | BNode, str]:
    """Create stable local Wikibase draft IDs for RDF resources."""
    seen: defaultdict[str, int] = defaultdict(int)
    local_ids: dict[URIRef | BNode, str] = {}
    for node in nodes:
        base = safe_local_id(_node_local_name(node))
        seen[base] += 1
        suffix = "" if seen[base] == 1 else f"_{seen[base]}"
        local_ids[node] = f"QDraft_{base}{suffix}"
    return local_ids


def _preferred_class_uri(graph: Graph, subject: URIRef | BNode) -> URIRef:
    """Choose the most informative RDF class for a draft entity."""
    types = {
        class_uri
        for class_uri in graph.objects(subject, RDF.type)
        if isinstance(class_uri, URIRef) and class_uri not in SKIPPED_SCHEMA_TYPES
    }
    for preferred in PREFERRED_CLASS_ORDER:
        if preferred in types:
            return preferred
    if types:
        return sorted(types, key=str)[0]
    return URIRef(f"{HM}UnknownHmoEntity")


_MAX_LABEL_LENGTH = 250  # Wikibase's hard cap on label length.


def _labels_for_node(graph: Graph, subject: URIRef | BNode) -> dict[str, str]:
    """Collect RDF labels, falling back to a readable URI local name."""
    labels: dict[str, str] = {}
    for label in graph.objects(subject, RDFS.label):
        if not isinstance(label, Literal):
            continue
        language = label.language or "en"
        labels.setdefault(language, str(label))
    if not labels:
        labels = {"en": _node_local_name(subject).replace("_", " ")}
    if "en" not in labels:
        labels["en"] = labels.get("he") or next(iter(labels.values()))
    return sanitize_monolingual_map(
        {lang: _truncate(text, _MAX_LABEL_LENGTH) for lang, text in labels.items()}
    )


def _descriptions_for_node(
    graph: Graph, subject: URIRef | BNode, class_uri: URIRef,
) -> dict[str, str]:
    """Collect RDF descriptions, falling back to a generic per-class one.

    ``rdfs:comment`` on the node itself (manuscript summaries, condition
    notes, etc.) is the real, curator-written source of truth when
    present. Otherwise fall back to a short class-based description —
    NOT the earlier "Offline HMO Wikibase draft for X" wording, which
    reads as leftover internal/debug text once shipped to the live
    Wikibase.
    """
    descriptions: dict[str, str] = {}
    for comment in graph.objects(subject, RDFS.comment):
        if not isinstance(comment, Literal):
            continue
        language = comment.language or "en"
        descriptions.setdefault(language, _truncate(str(comment), _MAX_DESCRIPTION_LENGTH))
    if descriptions:
        return sanitize_monolingual_map(descriptions)
    readable = local_name(class_uri).replace("_", " ")
    return {"en": f"{readable} in the Hebrew Manuscripts Ontology (HMO)"}


_MAX_DESCRIPTION_LENGTH = 250  # Wikibase's hard cap on description length.
_MAX_STRING_VALUE_LENGTH = 400  # Wikibase's default string/monolingualtext claim-value cap.


def _truncate(text: str, max_length: int) -> str:
    """Clip free-text (e.g. a MARC 520 summary, or an overlong label) to
    a Wikibase string-field length cap, preserving a visual ellipsis."""
    text = text.strip()
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def _statement_from_triple(
    predicate: Node,
    obj: Node,
    local_ids: dict[URIRef | BNode, str],
) -> WikibaseStatementDraft:
    """Convert an RDF predicate/object pair into a local statement draft."""
    property_uri = str(predicate)
    property_name = local_name(predicate)
    if isinstance(obj, Literal):
        value, datatype = _literal_value(obj)
        return WikibaseStatementDraft(
            property_name=property_name,
            property_uri=property_uri,
            value=value,
            value_type="literal",
            datatype=datatype,
            language=obj.language,
        )
    if isinstance(obj, URIRef | BNode) and obj in local_ids:
        return WikibaseStatementDraft(
            property_name=property_name,
            property_uri=property_uri,
            value=local_ids[obj],
            value_type="entity",
            value_entity_id=local_ids[obj],
        )
    if isinstance(obj, URIRef):
        return WikibaseStatementDraft(
            property_name=property_name,
            property_uri=property_uri,
            value=str(obj),
            value_type="uri",
        )
    return WikibaseStatementDraft(
        property_name=property_name,
        property_uri=property_uri,
        value=str(obj),
        value_type="blank",
    )


def _literal_value(literal: Literal) -> tuple[StatementValue, str | None]:
    """Convert an RDF literal into a JSON-safe scalar plus datatype URI."""
    value = literal.toPython()
    datatype = str(literal.datatype) if literal.datatype is not None else None
    if isinstance(value, bool | int | float | str):
        return value, datatype
    return str(value), datatype


def _node_local_name(node: URIRef | BNode) -> str:
    """Return a readable local name for a URI or blank node."""
    if isinstance(node, BNode):
        return f"BlankNode_{node}"
    return local_name(node)


# ── Phase 4: resolve drafts against the live schema ─────────────────────


def resolve_against_mappings(
    drafts: list[WikibaseEntityDraft],
    schema_mappings: dict[str, SchemaMappingEntry],
) -> list[ResolvedWikibaseEntity]:
    """Resolve offline drafts into real-PID/QID-shaped entities.

    Every ``class_uri``/statement ``property_uri`` MUST already have a
    live schema mapping (Phase 3's bootstrap) — any that don't are
    collected and raised as one :class:`UnmappedOntologyUriError` at the
    end, rejecting the whole batch rather than silently dropping
    statements (guards against a stale bootstrap after ontology growth).

    Statements whose ``value_type == "entity"`` (pointing at another
    draft in this same batch) are deferred: their target has no live QID
    yet, so the claim is recorded as a :class:`DeferredItemLink` for the
    upload path's second pass (Phase 5) instead of a direct claim.
    """
    missing_uris: set[str] = set()
    resolved: list[ResolvedWikibaseEntity] = []

    for draft in drafts:
        class_entry = schema_mappings.get(draft.class_uri)
        if class_entry is None:
            missing_uris.add(draft.class_uri)
            continue

        claims: list[ResolvedClaim] = []
        deferred: list[DeferredItemLink] = []
        skipped: list[str] = []
        for stmt in draft.statements:
            prop_entry = schema_mappings.get(stmt.property_uri)
            if prop_entry is None:
                missing_uris.add(stmt.property_uri)
                continue

            if stmt.value_type == "entity":
                # By construction (hmo_exporter._statement_from_triple),
                # value_type == "entity" only when the target is another
                # draft in this same batch — always a same-run instance.
                deferred.append(
                    DeferredItemLink(
                        source_local_id=draft.local_id,
                        property_id=prop_entry.wikibase_id,
                        target_local_id=stmt.value_entity_id or "",
                    )
                )
                continue

            claim = _build_claim_spec(prop_entry, stmt)
            if claim is None:
                skipped.append(
                    f"{stmt.property_name}: could not shape value "
                    f"{stmt.value!r} for datatype {prop_entry.datatype!r}"
                )
                continue
            claims.append(claim)

        source_uri_entry = schema_mappings.get(HMO_SOURCE_URI)
        if source_uri_entry is None:
            # Bootstrap ordering: an old database may not have this new
            # property mapped yet — skip gracefully instead of failing
            # the whole batch the way an unmapped ontology URI does.
            skipped.append(
                "hmo_source_uri: no live schema mapping yet — run the "
                "schema bootstrap to enable live-Wikibase reconciliation"
            )
        else:
            claims.append(
                ResolvedClaim(source_uri_entry.wikibase_id, "string", draft.source_uri)
            )

        resolved.append(
            ResolvedWikibaseEntity(
                local_id=draft.local_id,
                labels=draft.labels,
                descriptions=draft.descriptions,
                class_qid=class_entry.wikibase_id,
                source_uri=draft.source_uri,
                claims=claims,
                deferred_links=deferred,
                skipped_statements=skipped,
            )
        )

    if missing_uris:
        raise UnmappedOntologyUriError(sorted(missing_uris))

    return resolved


def _build_claim_spec(
    prop_entry: SchemaMappingEntry, stmt: WikibaseStatementDraft
) -> ResolvedClaim | None:
    """Shape one non-entity statement value for its property's datatype.

    Returns ``None`` when the value can't be shaped for the datatype
    (e.g. an object property whose value is an external URI, not an
    in-batch instance) — the caller records this as a skipped statement
    rather than failing the whole entity.
    """
    datatype = prop_entry.datatype or "string"
    if datatype == "wikibase-item":
        return None
    if datatype == "time":
        time_value = _parse_time_value(stmt.value)
        if time_value is None:
            return None
        return ResolvedClaim(prop_entry.wikibase_id, "time", time_value)
    if datatype == "monolingualtext":
        return ResolvedClaim(
            prop_entry.wikibase_id,
            "monolingualtext",
            {
                "text": _truncate(str(stmt.value), _MAX_STRING_VALUE_LENGTH),
                "language": stmt.language or "en",
            },
        )
    if datatype in ("string", "url", "external-id"):
        return ResolvedClaim(
            prop_entry.wikibase_id, datatype,
            _truncate(str(stmt.value), _MAX_STRING_VALUE_LENGTH),
        )
    if datatype == "quantity":
        try:
            amount = float(stmt.value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return ResolvedClaim(prop_entry.wikibase_id, "quantity", {"amount": amount})
    if datatype == "boolean":
        if isinstance(stmt.value, bool):
            return ResolvedClaim(prop_entry.wikibase_id, "boolean", stmt.value)
        text = str(stmt.value).strip().lower()
        if text in {"true", "1", "yes"}:
            return ResolvedClaim(prop_entry.wikibase_id, "boolean", True)
        if text in {"false", "0", "no"}:
            return ResolvedClaim(prop_entry.wikibase_id, "boolean", False)
        return None
    return None


_FULL_DATE_RE = re.compile(r"^(-?\d{3,4})-(\d{2})-(\d{2})")
_YEAR_RE = re.compile(r"(-?\d{3,4})")


def _parse_time_value(value: Any) -> dict[str, Any] | None:
    """Best-effort parse of a literal into a Wikibase ``time`` value.

    Handles full ``YYYY-MM-DD`` dates (day precision) and bare years
    (year precision) — the two shapes ``_literal_value`` actually
    produces for HMO's date-bearing literals. Returns ``None`` when
    neither pattern matches rather than guessing.
    """
    text = str(value)
    full = _FULL_DATE_RE.match(text)
    if full:
        year, month, day = full.groups()
        return {"time": f"+{int(year):04d}-{month}-{day}T00:00:00Z", "precision": 11}
    year_only = _YEAR_RE.search(text)
    if year_only:
        year = int(year_only.group(1))
        sign = "-" if year < 0 else "+"
        return {"time": f"{sign}{abs(year):04d}-00-00T00:00:00Z", "precision": 9}
    return None
