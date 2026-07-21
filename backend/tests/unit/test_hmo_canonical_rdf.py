import pytest
from rdflib import Literal, URIRef

from app.pipeline.hmo_canonical import normalize_live_entity
from app.pipeline.hmo_canonical_rdf import HMO_SOURCE_URI, graph_from_canonical_entities


def test_canonical_snapshot_projects_labels_claims_and_source_uri() -> None:
    entity = normalize_live_entity({
        "local_id": "Person_A",
        "source_uri": "https://w3id.org/mhm/ontology#Person_A",
        "wikibase_id": "Q1252",
        "labels": {"en": "A"},
        "descriptions": {"en": "person"},
        "claims": [{"property_uri": "https://w3id.org/mhm/ontology#viaf_id", "value": "123"}],
    })
    graph = graph_from_canonical_entities([entity])
    subject = URIRef(entity.source_uri)
    assert (subject, HMO_SOURCE_URI, Literal(entity.source_uri)) in graph
    assert len(graph) == 4


def test_canonical_projection_rejects_malformed_claims() -> None:
    entity = normalize_live_entity({
        "local_id": "Person_A",
        "source_uri": "https://w3id.org/mhm/ontology#Person_A",
        "wikibase_id": "Q1252",
        "claims": [{"property_uri": "https://w3id.org/mhm/ontology#viaf_id"}],
    })
    with pytest.raises(ValueError, match="missing value"):
        graph_from_canonical_entities([entity])
