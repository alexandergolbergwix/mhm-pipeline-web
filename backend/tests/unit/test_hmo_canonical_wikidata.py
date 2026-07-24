from app.pipeline.hmo_canonical import normalize_live_entity
from app.pipeline.hmo_canonical_wikidata import (
    canonical_studio_context,
    canonical_wikidata_fingerprint,
    native_items_from_hmo,
    native_wikidata_claims,
    quickstatements_from_canonical,
    uploadable_entities_from_hmo,
    wikidata_candidates_from_hmo,
)


def _person_entity(**extra: object):
    base = {
        "local_id": "Person_A",
        "source_uri": "https://w3id.org/mhm/ontology#Person_A",
        "wikibase_id": "Q1252",
        "entity_type": "E21_Person",
        "labels": {"en": "A Person"},
        "authority_evidence": [
            {"kind": "viaf", "identifier": "123456789", "accepted": True},
        ],
    }
    base.update(extra)
    return normalize_live_entity(base)


def _manuscript_entity(**extra: object):
    base = {
        "local_id": "QDraft_MS_990001",
        "source_uri": "https://w3id.org/mhm/ontology#MS_990001",
        "wikibase_id": "Q9001",
        "entity_type": "F4_Manifestation_Singleton",
        "labels": {"he": "כתב יד"},
        "control_numbers": ["990001"],
        "descriptions": {"en": "Offline HMO Wikibase draft for F4_Manifestation_Singleton"},
    }
    base.update(extra)
    return normalize_live_entity(base)


def test_wikidata_projection_filters_unaccepted_evidence_and_maps_entity_type() -> None:
    entity = _person_entity(
        authority_evidence=[
            {"kind": "wikidata", "identifier": "Q42", "accepted": True},
            {"kind": "wikidata", "identifier": "Q43", "accepted": False},
        ],
    )
    result = wikidata_candidates_from_hmo([entity])
    assert len(result) == 1
    assert result[0]["projection_source"] == "hmo_wikibase"
    assert result[0]["entity_type"] == "person"
    assert [row["identifier"] for row in result[0]["authority_evidence"]] == ["Q42"]


def test_canonical_wikidata_fingerprint_changes_with_live_claims() -> None:
    base = _person_entity()
    changed = _person_entity(claims=[{"property_uri": "https://w3id.org/mhm/ontology#viaf_id", "value": "999888777"}])
    assert canonical_wikidata_fingerprint([base]) != canonical_wikidata_fingerprint([changed])


def test_uploadable_filter_excludes_internal_graph_nodes_and_unidentified_persons() -> None:
    manuscript = _manuscript_entity()
    person = _person_entity()
    codicological = normalize_live_entity({
        "local_id": "CU_1",
        "source_uri": "https://w3id.org/mhm/ontology#CU_1",
        "wikibase_id": "Q77",
        "entity_type": "Codicological_Unit",
    })
    unidentified = normalize_live_entity({
        "local_id": "Person_unknown",
        "source_uri": "https://w3id.org/mhm/ontology#Person_unknown",
        "wikibase_id": "Q88",
        "entity_type": "E21_Person",
        "labels": {"en": "Unknown"},
    })
    uploadable = uploadable_entities_from_hmo([manuscript, person, codicological, unidentified])
    assert [entity.local_id for entity in uploadable] == ["QDraft_MS_990001", "Person_A"]


def test_native_claims_map_hmo_properties_and_control_numbers() -> None:
    manuscript = _manuscript_entity(
        claims=[
            {"property_uri": "https://w3id.org/mhm/ontology#shelfmark", "value": "Heb. 4"},
        ],
    )
    claims = native_wikidata_claims(manuscript)
    assert {"property": "P3959", "value": "990001"} in claims
    assert {"property": "P217", "value": "Heb. 4"} in claims
    assert {"property": "P31", "value": "Q87167"} in claims


def test_existing_qid_reads_identifier_field_from_evidence() -> None:
    person = _person_entity(
        authority_evidence=[{"kind": "wikidata", "identifier": "Q1218", "accepted": True}],
    )
    items = native_items_from_hmo([person])
    assert len(items) == 1
    assert items[0].existing_qid == "Q1218"
    assert items[0].entity_type == "person"


def test_descriptions_drop_offline_boilerplate_and_use_marc_when_available() -> None:
    manuscript = _manuscript_entity()
    context = canonical_studio_context(
        marc_records=[{
            "_control_number": "990001",
            "languages": ["heb"],
            "dates": {"original_string": "16th century"},
            "script_type": "sephardi",
            "materials": ["parchment"],
        }],
    )
    items = native_items_from_hmo([manuscript], context=context)
    description = items[0].descriptions["en"]
    assert "Offline" not in description
    assert "draft" not in description.lower()
    assert "16th century" in description
    assert "parchment" in description
    assert description.startswith("Hebrew manuscript")


def test_full_canonical_chain_has_no_legacy_authority_dependency() -> None:
    from app.pipeline.hmo_canonical_rdf import graph_from_canonical_entities

    entity = _person_entity(
        labels={"en": "Jerusalem", "he": "ירושלים"},
        descriptions={"en": "Historic place"},
        authority_evidence=[
            {"kind": "mazal", "identifier": "987007270341205171", "accepted": True},
        ],
        claims=[
            {
                "property_uri": "https://w3id.org/mhm/ontology#instance_of",
                "wikidata_property": "P31",
                "value_type": "wikibase-item",
                "target_qid": "Q515",
                "value": "Q515",
            },
        ],
    )
    graph = graph_from_canonical_entities([entity])
    candidates = wikidata_candidates_from_hmo([entity])
    assert len(graph) > 0
    assert candidates[0]["hmo_wikibase_id"] == "Q1252"
    assert {"property": "P31", "value": "Q515"} in native_wikidata_claims(entity)
    assert 'LAST\tP31\t"Q515"' in quickstatements_from_canonical([entity])
