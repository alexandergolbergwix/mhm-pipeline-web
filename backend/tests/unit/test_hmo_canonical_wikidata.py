from app.pipeline.hmo_canonical import normalize_live_entity
from app.pipeline.hmo_canonical_wikidata import (
    PUBLIC_WIKIDATA_ENTITY_TYPES,
    build_canonical_studio_result,
    canonical_studio_context,
    canonical_wikidata_fingerprint,
    filter_public_wikidata_items,
    native_items_from_hmo,
    native_wikidata_claims,
    quickstatements_from_canonical,
    studio_cache_has_non_public_items,
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


def test_filter_public_wikidata_items_drops_hmo_classes() -> None:
    rows = [
        {"local_id": "ms1", "entity_type": "manuscript"},
        {"local_id": "cu1", "entity_type": "Codicological_Unit"},
        {"local_id": "p1", "entity_type": "E21_Person"},
        {"local_id": "w1", "entity_type": "work"},
    ]
    filtered = filter_public_wikidata_items(rows, source="canonical")
    assert [row["local_id"] for row in filtered] == ["ms1", "w1"]


def test_studio_cache_has_non_public_items_detects_stale_canonical_cache() -> None:
    rows = [
        {"local_id": "ms1", "entity_type": "manuscript"},
        {"local_id": "view1", "entity_type": "PhilologicalView"},
    ]
    assert studio_cache_has_non_public_items(rows, source="canonical") is True
    assert studio_cache_has_non_public_items(rows, source="legacy") is False


def test_canonical_person_labels_uninvert_and_route_script() -> None:
    person = _person_entity(labels={"he": "מלינובסקי, יוסף בן מרדכי"})
    items = native_items_from_hmo([person])
    assert len(items) == 1
    assert items[0].labels.get("he") == "יוסף בן מרדכי מלינובסקי"
    assert "en" not in items[0].labels or not any(
        "\u0590" <= c <= "\u05ff" for c in items[0].labels.get("en", "")
    )
    assert "מלינובסקי, יוסף בן מרדכי" in (items[0].aliases.get("he") or [])


def test_canonical_manuscript_en_label_uses_shelfmark_not_hebrew_title() -> None:
    manuscript = _manuscript_entity(
        labels={"en": '"גלא עמיקתא (חלק א, ב)."', "he": "גלא עמיקתא (חלק א, ב)"},
        claims=[{"property_uri": "https://w3id.org/mhm/ontology#shelfmark", "value": "Heb. 8° 1234"}],
    )
    context = canonical_studio_context(
        marc_records=[{
            "_control_number": "990001",
            "title": "גלא עמיקתא (חלק א, ב)",
            "shelfmark": "Heb. 8° 1234",
        }],
    )
    items = native_items_from_hmo([manuscript], context=context)
    assert items[0].labels["he"] == "גלא עמיקתא (חלק א, ב)"
    assert items[0].labels["en"] == "Jerusalem, NLI, Heb. 8° 1234"
    assert not any("\u0590" <= c <= "\u05ff" for c in items[0].labels["en"])


def test_canonical_latin_title_does_not_land_in_he_label() -> None:
    manuscript = _manuscript_entity(
        labels={"he": "Meir Netiv in Latin.", "en": "Meir Netiv in Latin."},
        claims=[{"property_uri": "https://w3id.org/mhm/ontology#shelfmark", "value": "Ms. Or. 9"}],
    )
    context = canonical_studio_context(
        marc_records=[{
            "_control_number": "990001",
            "title": "Meir Netiv in Latin.",
            "shelfmark": "Ms. Or. 9",
        }],
    )
    items = native_items_from_hmo([manuscript], context=context)
    assert items[0].labels["en"] == "Jerusalem, NLI, Ms. Or. 9"
    assert "he" not in items[0].labels or any(
        "\u0590" <= c <= "\u05ff" for c in items[0].labels.get("he", "א")
    )


def test_canonical_work_stamps_505_evidence_and_drops_unevidenced_creates() -> None:
    work_ok = normalize_live_entity({
        "local_id": "Work_505",
        "source_uri": "https://w3id.org/mhm/ontology#Work_505",
        "wikibase_id": "Q5051",
        "entity_type": "F1_Work",
        "labels": {"he": "משנה תורה"},
        "control_numbers": ["990001"],
    })
    work_bad = normalize_live_entity({
        "local_id": "Work_orphan",
        "source_uri": "https://w3id.org/mhm/ontology#Work_orphan",
        "wikibase_id": "Q5052",
        "entity_type": "F1_Work",
        "labels": {"he": "יצירה ללא מקור"},
        "control_numbers": ["990001"],
    })
    context = canonical_studio_context(
        marc_records=[{
            "_control_number": "990001",
            "contents": [{"title": "משנה תורה", "folio": "1r", "sequence": 1}],
        }],
    )
    items = native_items_from_hmo([work_ok, work_bad], context=context)
    assert [item.local_id for item in items] == ["Work_505"]
    evidence = items[0].work_candidate_evidence
    assert evidence and evidence[0]["accepted"] is True
    assert evidence[0]["reason"] == "named_work_in_505"
    assert items[0].labels.get("he") == "משנה תורה"
    assert "en" not in items[0].labels


def test_canonical_work_with_existing_qid_kept_without_marc_join() -> None:
    work = normalize_live_entity({
        "local_id": "Work_qid",
        "source_uri": "https://w3id.org/mhm/ontology#Work_qid",
        "wikibase_id": "Q5053",
        "entity_type": "F1_Work",
        "labels": {"he": "תנ״ך"},
        "authority_evidence": [
            {"kind": "wikidata", "identifier": "Q83367", "accepted": True},
        ],
    })
    items = native_items_from_hmo([work])
    assert len(items) == 1
    assert items[0].existing_qid == "Q83367"
    assert items[0].work_candidate_evidence[0]["accepted"] is True


def test_build_canonical_result_has_no_blocking_label_or_work_errors() -> None:
    from converter.wikidata.item_validator import validate_item

    manuscript = _manuscript_entity(
        labels={"he": "שער שברי לוחות"},
        claims=[{"property_uri": "https://w3id.org/mhm/ontology#shelfmark", "value": "Heb. 4° 1"}],
    )
    person = _person_entity(labels={"he": "כרמי, ישראל בן יוסף"})
    work = normalize_live_entity({
        "local_id": "Work_ok",
        "source_uri": "https://w3id.org/mhm/ontology#Work_ok",
        "wikibase_id": "Q5054",
        "entity_type": "F1_Work",
        "labels": {"he": "מלאכת שלמה"},
        "control_numbers": ["990001"],
    })
    context = canonical_studio_context(
        marc_records=[{
            "_control_number": "990001",
            "title": "שער שברי לוחות",
            "shelfmark": "Heb. 4° 1",
            "contents": [{"title": "מלאכת שלמה"}],
        }],
    )
    result = build_canonical_studio_result(
        [manuscript, person, work], context=context, reconcile=False, return_native=True,
    )
    assert result["summary"]["total_items"] == 3
    for item in result["native_items"] or []:
        codes = {issue.code for issue in validate_item(item)}
        assert "EN_LABEL_IS_HEBREW" not in codes
        assert "INVERTED_NAME_LABEL" not in codes
        assert "HE_LABEL_IS_LATIN" not in codes
        assert "WORK_WITHOUT_SOURCE_EVIDENCE" not in codes
    for row in result["items"]:
        issues = row.get("validation_issues") or []
        codes = {str(i.get("code")) for i in issues}
        assert "EN_LABEL_IS_HEBREW" not in codes
        assert "INVERTED_NAME_LABEL" not in codes
        assert "WORK_WITHOUT_SOURCE_EVIDENCE" not in codes


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
