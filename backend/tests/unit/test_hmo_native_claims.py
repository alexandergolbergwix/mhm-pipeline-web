from app.pipeline.hmo_canonical import normalize_live_entity
from app.pipeline.hmo_canonical_wikidata import native_wikidata_claims


def _manuscript(claims: list[dict[str, object]]) -> object:
    return normalize_live_entity({
        "local_id": "Manuscript_A",
        "source_uri": "https://w3id.org/mhm/ontology#Manuscript_A",
        "wikibase_id": "Q1252",
        "entity_type": "F4_Manifestation_Singleton",
        "control_numbers": ["990001"],
        "labels": {"en": "A manuscript"},
        "claims": claims,
    })


def test_native_claim_mapping_requires_explicit_property_and_value() -> None:
    """Unmapped ontology URIs and malformed QIDs never become claims."""
    entity = _manuscript([
        {"property_uri": "https://w3id.org/mhm/ontology#viaf_id", "value": "123"},
        {"wikidata_property": "P31", "value_type": "wikibase-item", "target_qid": "not-a-qid"},
        {"wikidata_property": "P217", "value_type": "string", "value": "F 1"},
    ])
    assert native_wikidata_claims(entity) == [
        {"property": "P217", "value": "F 1", "value_type": "string"},
        {"property": "P3959", "value": "990001"},
        {"property": "P31", "value": "Q87167"},
    ]


def test_manuscript_never_carries_p50() -> None:
    """Rule W-98 — a manuscript links to a work (P1574), never to an author."""
    entity = _manuscript([
        {"wikidata_property": "P50", "value_type": "wikibase-item", "target_qid": "Q42"},
    ])
    assert "P50" not in {claim["property"] for claim in native_wikidata_claims(entity)}


def test_person_keeps_its_authority_identifier_claims() -> None:
    entity = normalize_live_entity({
        "local_id": "Person_A",
        "source_uri": "https://w3id.org/mhm/ontology#Person_A",
        "wikibase_id": "Q7",
        "entity_type": "E21_Person",
        "control_numbers": ["990001"],
        "labels": {"en": "A person"},
        "claims": [{"wikidata_property": "P214", "value": "123"}],
    })
    assert native_wikidata_claims(entity) == [
        {"property": "P214", "value": "123", "value_type": "string"},
        {"property": "P31", "value": "Q5"},
    ]


def test_quickstatements_uses_only_native_claims() -> None:
    from app.pipeline.hmo_canonical_wikidata import quickstatements_from_canonical

    entity = _manuscript([
        {"property_uri": "https://w3id.org/mhm/ontology#viaf_id", "value": "123"},
        {"wikidata_property": "P217", "value_type": "string", "value": "F 1"},
    ])
    text = quickstatements_from_canonical([entity])
    assert 'LAST\tP217\t"F 1"' in text
    assert 'LAST\tP3959\t"990001"' in text
    assert "viaf_id" not in text
