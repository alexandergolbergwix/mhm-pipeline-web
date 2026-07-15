"""Phase 1 evidence-gating and MARC projection regressions."""

from converter.wikidata.item_builder import WikidataItemBuilder


def _statements(item: object, property_id: str) -> list[object]:
    return [statement for statement in item.statements if statement.property_id == property_id]


def test_unsupported_genre_does_not_create_p136_or_illuminated_p31() -> None:
    item = WikidataItemBuilder().build_manuscript_item({
        "_control_number": "GENRE-GUARD",
        "title": "כתב יד",
        "genres": ["Illustrated works (Manuscript)"],
        "notes": ["שער מעוטר בדיו"],
        "has_decoration": True,
    })
    assert [statement.value for statement in _statements(item, "P136")] == []
    assert all(statement.value != "Q48498" for statement in _statements(item, "P31"))


def test_catalog_workflow_text_does_not_become_p1684() -> None:
    item = WikidataItemBuilder().build_manuscript_item({
        "_control_number": "NOTE-GUARD",
        "title": "כתב יד",
        "colophon_text": "נושא נוסף: כתב-יד. מכירה",
        "scribal_interventions": [{"text": "Book suggested to Google; rejected", "type": "note"}],
    })
    assert _statements(item, "P1684") == []


def test_former_owner_and_censor_are_not_current_p127() -> None:
    item = WikidataItemBuilder().build_manuscript_item({
        "_control_number": "ROLE-GUARD",
        "title": "כתב יד",
        "marc_authority_matches": [
            {
                "name": "Former Owner",
                "role": "former owner",
                "wikidata_qid": "Q100",
                "entity_kind": "person",
            },
            {
                "name": "Censor",
                "role": "CENSOR",
                "wikidata_qid": "Q101",
                "entity_kind": "person",
            },
        ],
    })
    assert _statements(item, "P127") == []


def test_external_current_holder_uses_verified_p195_and_description() -> None:
    item = WikidataItemBuilder().build_manuscript_item({
        "_control_number": "HOLDER-GUARD",
        "title": "כתב יד",
        "contributors": [{"name": "The Russian State Library", "role": "current owner"}],
        "marc_authority_matches": [{
            "name": "The Russian State Library",
            "role": "current owner",
            "wikidata_qid": "Q182",
            "entity_kind": "organization",
        }],
    })
    assert [statement.value for statement in _statements(item, "P195")] == ["Q182"]
    assert "Russian State Library" in item.descriptions["en"]


def test_external_current_holder_without_qid_is_not_defaulted_to_nli() -> None:
    item = WikidataItemBuilder().build_manuscript_item({
        "_control_number": "HOLDER-EVIDENCE",
        "title": "כתב יד",
        "contributors": [{"name": "The Russian State Library", "role": "current owner"}],
    })
    assert _statements(item, "P195") == []
    assert "Russian State Library" in item.descriptions["en"]
