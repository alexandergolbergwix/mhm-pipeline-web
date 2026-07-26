from app.pipeline.hmo_canonical import normalize_live_entity
from app.pipeline.hmo_canonical_wikidata import (
    PUBLIC_WIKIDATA_ENTITY_TYPES,
    build_canonical_studio_result,
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
            {
                "property_uri": "https://w3id.org/mhm/ontology#has_author",
                "wikidata_value": "https://www.wikidata.org/entity/Q42",
                "value_type": "wikibase-item",
            },
            {
                "property_id": "P12",
                "target_qid": "Q1252",
                "value_type": "wikibase-item",
            },
        ],
    )
    claims = native_wikidata_claims(manuscript)
    assert {"property": "P3959", "value": "990001"} in claims
    assert {"property": "P217", "value": "Heb. 4"} in claims
    assert {"property": "P31", "value": "Q87167"} in claims
    # Manuscript must never emit P50; bare project QIDs must never leak.
    assert not any(c["property"] == "P50" for c in claims)
    assert not any(c["value"] == "Q1252" for c in claims)


def test_native_claims_map_project_pid_via_ontology_ledger() -> None:
    manuscript = _manuscript_entity(
        claims=[{"property_id": "P7", "value": "Ms. Or. 12"}],
    )
    claims = native_wikidata_claims(
        manuscript,
        ontology_uri_by_project_pid={
            "P7": "https://w3id.org/mhm/ontology#shelfmark",
        },
    )
    assert {"property": "P217", "value": "Ms. Or. 12"} in claims


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


def test_summarized_production_rolls_onto_manuscript_claims() -> None:
    manuscript = _manuscript_entity()
    production = normalize_live_entity({
        "local_id": "Prod_1",
        "source_uri": "https://w3id.org/mhm/ontology#Prod_1",
        "wikibase_id": "Q7001",
        "entity_type": "E12_Production",
        "control_numbers": ["990001"],
        "claims": [
            {
                "property_uri": "https://w3id.org/mhm/ontology#has_date_of_creation",
                "value": "1600",
            },
            {
                "property_uri": "https://w3id.org/mhm/ontology#has_location_of_creation",
                "wikidata_value": "https://www.wikidata.org/entity/Q1218",
                "value_type": "wikibase-item",
            },
            {
                "property_uri": "https://w3id.org/mhm/ontology#has_scribe",
                "wikidata_value": "https://www.wikidata.org/entity/Q42",
                "value_type": "wikibase-item",
            },
        ],
    })
    claims = native_wikidata_claims(manuscript, rollup_sources=[production])
    assert {"property": "P571", "value": "1600"} in claims
    assert {"property": "P1071", "value": "Q1218"} in claims
    assert {"property": "P11603", "value": "Q42"} in claims
    assert not any(c["property"] == "P50" for c in claims)


def test_codicological_unit_alone_is_not_a_studio_item() -> None:
    cu = normalize_live_entity({
        "local_id": "CU_rollup",
        "source_uri": "https://w3id.org/mhm/ontology#CU_rollup",
        "wikibase_id": "Q77",
        "entity_type": "Codicological_Unit",
        "control_numbers": ["990001"],
        "claims": [
            {
                "property_uri": "https://w3id.org/mhm/ontology#has_number_of_folios",
                "value": "120",
            },
        ],
    })
    manuscript = _manuscript_entity()
    uploadable = uploadable_entities_from_hmo([manuscript, cu])
    assert [entity.local_id for entity in uploadable] == ["QDraft_MS_990001"]
    items = native_items_from_hmo([manuscript, cu])
    folio_claims = [s for s in items[0].statements if s.property_id == "P1104"]
    assert folio_claims
    assert folio_claims[0].value == "120"


def test_fingerprint_changes_when_rolled_up_claim_changes() -> None:
    manuscript = _manuscript_entity()
    production = normalize_live_entity({
        "local_id": "Prod_fp",
        "source_uri": "https://w3id.org/mhm/ontology#Prod_fp",
        "wikibase_id": "Q7002",
        "entity_type": "E12_Production",
        "control_numbers": ["990001"],
        "claims": [
            {
                "property_uri": "https://w3id.org/mhm/ontology#has_date_of_creation",
                "value": "1700",
            },
        ],
    })
    without = canonical_wikidata_fingerprint([manuscript])
    with_prod = canonical_wikidata_fingerprint([manuscript, production])
    assert without != with_prod


def test_manuscript_bridge_statements_include_p2888_and_p973() -> None:
    items = native_items_from_hmo([_manuscript_entity()])
    props = {stmt.property_id for stmt in items[0].statements}
    assert "P2888" in props
    assert "P973" in props


def test_build_summary_reports_rollup_counts() -> None:
    manuscript = _manuscript_entity()
    production = normalize_live_entity({
        "local_id": "Prod_sum",
        "source_uri": "https://w3id.org/mhm/ontology#Prod_sum",
        "wikibase_id": "Q7003",
        "entity_type": "E12_Production",
        "control_numbers": ["990001"],
        "claims": [],
    })
    result = build_canonical_studio_result([manuscript, production], reconcile=False)
    assert result["summary"]["rolled_up_entities"] == 1
    assert result["summary"]["summarized_hmo_nodes"] == 1
    assert all(
        item["entity_type"] in PUBLIC_WIKIDATA_ENTITY_TYPES
        for item in result["items"]
    )


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
