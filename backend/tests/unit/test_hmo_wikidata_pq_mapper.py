"""Tests for project HMO Wikibase → public Wikidata P/Q mapper."""

from __future__ import annotations

from converter.wikidata.hmo_wikidata_pq_mapper import (
    claim_allowed_for_entity_type,
    map_hmo_claim_to_wikidata,
    map_item_value_to_wikidata_qid,
    map_local_name_to_wikidata_pid,
    map_property_to_wikidata_pid,
)


def test_local_name_maps_to_wpm_pids() -> None:
    assert map_local_name_to_wikidata_pid("has_script_type") == "P9302"
    assert map_local_name_to_wikidata_pid("has_number_of_folios") == "P1104"
    assert map_local_name_to_wikidata_pid("mentions_scribe") == "P11603"
    assert map_local_name_to_wikidata_pid("has_author") == "P50"
    assert map_local_name_to_wikidata_pid("belongs_to_tradition") is None


def test_project_pid_maps_only_via_ontology_ledger() -> None:
    assert map_property_to_wikidata_pid(project_property_id="P42") is None
    assert map_property_to_wikidata_pid(
        project_property_id="P42",
        ontology_uri_by_project_pid={
            "P42": "https://w3id.org/mhm/ontology#has_material",
        },
    ) == "P186"
    assert map_property_to_wikidata_pid(
        wikidata_property="P214",
        project_property_id="P99",
    ) == "P214"


def test_bare_project_qid_never_becomes_wikidata_value() -> None:
    assert map_item_value_to_wikidata_qid(
        target_qid="Q1252",
        project_item_qid="Q9001",
    ) is None
    assert map_item_value_to_wikidata_qid(
        target_qid="Q1252",
        project_item_qid="Q1252",
    ) is None
    assert map_item_value_to_wikidata_qid(
        wikidata_value="https://www.wikidata.org/entity/Q1218",
        target_qid="Q1252",
        project_item_qid="Q9001",
    ) == "Q1218"
    assert map_item_value_to_wikidata_qid(
        target_uri="https://w3id.org/mhm/ontology#E21_Person",
    ) == "Q5"
    # Explicit public projection may carry a bare QID with wikidata_property.
    assert map_item_value_to_wikidata_qid(
        target_qid="Q515",
        wikidata_value="Q515",
        project_item_qid="Q1252",
        explicit_wikidata_claim=True,
    ) == "Q515"


def test_manuscript_forbids_p50_even_when_mapped() -> None:
    assert claim_allowed_for_entity_type("P50", "manuscript") is False
    assert claim_allowed_for_entity_type("P50", "work") is True
    mapped = map_hmo_claim_to_wikidata(
        {
            "property_uri": "https://w3id.org/mhm/ontology#has_author",
            "wikidata_value": "https://www.wikidata.org/entity/Q42",
            "value_type": "wikibase-item",
        },
        entity_type="manuscript",
    )
    assert mapped is None
    work = map_hmo_claim_to_wikidata(
        {
            "property_uri": "https://w3id.org/mhm/ontology#has_author",
            "wikidata_value": "https://www.wikidata.org/entity/Q42",
            "value_type": "wikibase-item",
        },
        entity_type="work",
    )
    assert work is not None
    assert work.property_id == "P50"
    assert work.value == "Q42"


def test_map_claim_via_project_pid_ledger() -> None:
    mapped = map_hmo_claim_to_wikidata(
        {"property_id": "P7", "value": "Heb. 4"},
        entity_type="manuscript",
        ontology_uri_by_project_pid={
            "P7": "https://w3id.org/mhm/ontology#shelfmark",
        },
    )
    assert mapped is not None
    assert mapped.property_id == "P217"
    assert mapped.value == "Heb. 4"


def test_exact_match_ontology_iri_rewrites_to_item_page() -> None:
    mapped = map_hmo_claim_to_wikidata(
        {
            "property_uri": "https://w3id.org/mhm/ontology#hmo_source_uri",
            "value": "https://w3id.org/mhm/ontology#MS_990001",
            "datatype": "url",
        },
        entity_type="manuscript",
        project_item_qid="Q894",
    )
    assert mapped is not None
    assert mapped.property_id == "P2888"
    assert mapped.value == "https://mhm-hmo.wikibase.cloud/wiki/Item:Q894"
    assert mapped.value_type == "url"


def test_exact_match_ontology_iri_dropped_without_qid() -> None:
    mapped = map_hmo_claim_to_wikidata(
        {
            "wikidata_property": "P2888",
            "value": "https://w3id.org/mhm/ontology#MS_990001",
            "datatype": "url",
        },
        entity_type="manuscript",
    )
    assert mapped is None


def test_catalog_temporary_record_never_becomes_inscription() -> None:
    mapped = map_hmo_claim_to_wikidata(
        {
            "property_uri": "https://w3id.org/mhm/ontology#colophon_text",
            "value": "רשומה זמנית",
        },
        entity_type="manuscript",
    )
    assert mapped is None


def test_inscription_forbidden_on_person() -> None:
    mapped = map_hmo_claim_to_wikidata(
        {
            "property_uri": "https://w3id.org/mhm/ontology#colophon_text",
            "value": "נשלם העתק זה",
        },
        entity_type="person",
    )
    assert mapped is None
