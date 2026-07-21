"""Deterministic RDF projection from canonical HMO Wikibase snapshots."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from rdflib import Graph, Literal, RDF, URIRef

from app.pipeline.hmo_canonical import CanonicalHmoEntity, assert_canonical_entities

HMO_SOURCE_URI = URIRef("https://w3id.org/mhm/ontology#hmo_source_uri")
HMO_LABEL = URIRef("http://www.w3.org/2000/01/rdf-schema#label")
HMO_DESCRIPTION = URIRef("http://www.w3.org/2000/01/rdf-schema#comment")


def graph_from_canonical_entities(
    entities: Iterable[CanonicalHmoEntity],
    *,
    ontology_uris: set[str] | None = None,
) -> Graph:
    """Project live HMO state without consulting MARC or authority rows.

    Claim dictionaries are expected to contain ``property_uri`` and either a
    URI ``target_uri`` or a scalar ``value``. Unsupported claims are rejected
    rather than silently dropped.
    """
    materialized = list(entities)
    assert_canonical_entities(materialized)
    graph = Graph()
    for entity in materialized:
        subject = URIRef(entity.source_uri)
        if ontology_uris is not None and entity.source_uri not in ontology_uris:
            raise ValueError(f"canonical entity URI is outside the ontology namespace: {entity.source_uri}")
        graph.add((subject, HMO_SOURCE_URI, Literal(entity.source_uri)))
        for language, label in entity.labels.items():
            graph.add((subject, HMO_LABEL, Literal(label, lang=language)))
        for language, description in entity.descriptions.items():
            graph.add((subject, HMO_DESCRIPTION, Literal(description, lang=language)))
        for claim in entity.claims:
            property_uri = str(claim.get("property_uri") or "").strip()
            if not property_uri:
                raise ValueError(f"canonical claim missing property_uri for {entity.local_id}")
            predicate = URIRef(property_uri)
            target_uri = str(claim.get("target_uri") or "").strip()
            if target_uri:
                value: URIRef | Literal = URIRef(target_uri)
            elif "value" in claim:
                value = Literal(claim["value"])
            else:
                raise ValueError(f"canonical claim missing value for {entity.local_id}: {property_uri}")
            graph.add((subject, predicate, value))
    return graph
