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


def test_transcription_fragments_merge_into_one_claim_with_reference() -> None:
    """60+ P1684 fragments drowned the judge's view of P31 (run 47e03437)."""
    from app.pipeline.hmo_canonical_wikidata import _merge_transcription_statements
    from converter.wikidata.item_models import WikidataStatement

    statements = [
        WikidataStatement(property_id="P1684", value="נשלם זה הסדור", value_type="monolingualtext"),
        WikidataStatement(property_id="P1684", value="נעתק זה הספר", value_type="monolingualtext"),
        WikidataStatement(property_id="P1684", value="רשומה זמנית", value_type="monolingualtext"),
        WikidataStatement(property_id="P217", value="F 1", value_type="string"),
        WikidataStatement(property_id="P31", value="Q87167", value_type="wikibase-item"),
    ]
    _merge_transcription_statements(statements, control_number="990001")
    p1684 = [s for s in statements if s.property_id == "P1684"]
    assert len(p1684) == 1
    assert p1684[0].value == "נשלם זה הסדור | נעתק זה הספר"
    assert p1684[0].references
    assert [s.property_id for s in statements] == ["P1684", "P217", "P31"]


def test_single_transcription_claim_still_gets_the_catalog_reference() -> None:
    from app.pipeline.hmo_canonical_wikidata import _merge_transcription_statements
    from converter.wikidata.item_models import WikidataStatement

    statements = [
        WikidataStatement(property_id="P1684", value="נשלם זה הסדור", value_type="monolingualtext"),
    ]
    _merge_transcription_statements(statements, control_number="990001")
    assert statements[0].references


def test_oversized_transcription_join_keeps_complete_fragments() -> None:
    from app.pipeline.hmo_canonical_wikidata import (
        _MERGED_TRANSCRIPTION_CHARS,
        _merge_transcription_statements,
    )
    from converter.wikidata.item_models import WikidataStatement

    statements = [
        WikidataStatement(property_id="P1684", value="א" * 3000, value_type="monolingualtext"),
        WikidataStatement(property_id="P1684", value="ב" * 3000, value_type="monolingualtext"),
    ]
    _merge_transcription_statements(statements, control_number="990001")
    assert len(statements) == 2
    assert [s.value for s in statements] == ["א" * 3000, "ב" * 3000]
    assert sum(len(s.value) for s in statements) > _MERGED_TRANSCRIPTION_CHARS


def test_duplicate_fragments_are_joined_once() -> None:
    from app.pipeline.hmo_canonical_wikidata import _merge_transcription_statements
    from converter.wikidata.item_models import WikidataStatement

    statements = [
        WikidataStatement(property_id="P1922", value="beginning", value_type="monolingualtext"),
        WikidataStatement(property_id="P1922", value="beginning", value_type="monolingualtext"),
    ]
    _merge_transcription_statements(statements, control_number="990001")
    assert len(statements) == 1
    assert statements[0].value == "beginning"


def test_english_description_never_embeds_hebrew_holder() -> None:
    """Rule W-70 — a Hebrew holder name is romanised or omitted, never quoted."""
    from converter.wikidata.item_builder import _build_manuscript_description

    record = {
        "languages": ["heb"],
        "dates": {"year": 1639},
        "holding_institution": "ורונה",
        "_control_number": "990001",
    }
    desc = _build_manuscript_description(record)
    assert not any("\u0590" <= ch <= "\u05ff" for ch in desc)
    assert "1639" in desc
