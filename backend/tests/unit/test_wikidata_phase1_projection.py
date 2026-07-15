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


def test_illustration_words_in_catalog_notes_do_not_prove_illumination() -> None:
    item = WikidataItemBuilder().build_manuscript_item({
        "_control_number": "GENRE-NOTE-GUARD",
        "title": "שער שברי לוחות",
        "genres": ["Illustrated works (Manuscript)"],
        "notes": ["ציורים ועטורים; שער מעוטר בדיו"],
        "has_decoration": True,
    })
    assert [statement.value for statement in _statements(item, "P136")] == []
    assert all(statement.value != "Q48498" for statement in _statements(item, "P31"))


def test_explicit_decoration_evidence_allows_illuminated_instance() -> None:
    item = WikidataItemBuilder().build_manuscript_item({
        "_control_number": "GENRE-CONFIRMED",
        "title": "כתב יד מעוטר",
        "genres": [{
            "term": "Illustrated works (Manuscript)",
            "wikidata_id": "Q48498",
            "evidence_supported": True,
        }],
        "genre_entries": [{
            "term": "Illustrated works (Manuscript)",
            "wikidata_id": "Q48498",
            "evidence_supported": True,
        }],
    })
    assert [statement.value for statement in _statements(item, "P136")] == ["Q48498"]
    assert [statement.value for statement in _statements(item, "P31")] == ["Q48498", "Q87167"]


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


def test_missing_holder_qid_does_not_default_to_nli_collection() -> None:
    item = WikidataItemBuilder().build_manuscript_item({
        "_control_number": "NLI-NO-FALLBACK",
        "title": "כתב יד",
    })
    assert _statements(item, "P195") == []


def test_canonical_nli_current_holder_emits_verified_collection() -> None:
    item = WikidataItemBuilder().build_manuscript_item({
        "_control_number": "NLI-CANONICAL",
        "title": "כתב יד",
        "contributors": [{
            "name": "The National Library of Israel",
            "role": "current owner",
        }],
    })
    assert [statement.value for statement in _statements(item, "P195")] == ["Q188915"]


def test_masorah_subject_is_promoted_from_verified_exact_term() -> None:
    item = WikidataItemBuilder().build_manuscript_item({
        "_control_number": "MASORAH-SUBJECT",
        "title": "כתב יד",
        "subjects": [{"term": "Masorah", "type": "topic"}],
    })
    assert [statement.value for statement in _statements(item, "P921")] == ["Q3850835"]


def test_printed_facsimile_is_not_typed_as_manuscript() -> None:
    item = WikidataItemBuilder().build_manuscript_item({
        "_control_number": "FACSIMILE",
        "title": "פנקס המדינה",
        "notes": ["דפוס צלום של הוצאת ברלין, תרפ\"ה"],
    })
    assert [statement.value for statement in _statements(item, "P31")] == ["Q571"]
    assert "printed facsimile edition" in item.descriptions["en"]


def test_marc_author_and_title_create_exemplar_work_chain() -> None:
    items = WikidataItemBuilder().build_all([{
        "_control_number": "AUTHOR-WORK-FALLBACK",
        "title": "שער שברי לוחות",
        "authors": [{"name": "אליהו בן אשר", "role": "author"}],
    }])
    work = next(item for item in items if item.entity_type == "work")
    manuscript = next(item for item in items if item.entity_type == "manuscript")
    assert any(statement.property_id == "P50" or statement.property_id == "P2093" for statement in work.statements)
    assert any(statement.property_id == "P1574" for statement in manuscript.statements)


def test_placeholder_holder_is_omitted_from_description() -> None:
    item = WikidataItemBuilder().build_manuscript_item({
        "_control_number": "UNKNOWN-HOLDER",
        "title": "הגדה של פסח",
        "holding_institution": "Unknown Library",
    })
    assert "Unknown Library" not in item.descriptions["en"]


def test_person_authority_preserves_inverted_and_latin_aliases() -> None:
    items = WikidataItemBuilder().build_all([{
        "_control_number": "PERSON-ALIASES",
        "title": "כתב יד",
        "marc_authority_matches": [{
            "name": "אליהו-קאולי, דליה",
            "mazal_id": "987007453092705171",
            "preferred_name_lat": "Eliyahu-Kauli, Dalia",
            "preferred_name_heb": "אליהו-קאולי, דליה",
            "name_type": "personal",
            "role": "scribe",
        }],
    }])
    item = next(item for item in items if item.entity_type == "person")
    assert item.labels["he"] == "דליה אליהו-קאולי"
    assert "אליהו-קאולי, דליה" in item.aliases["he"]
    assert "Eliyahu-Kauli, Dalia" in item.aliases["en"]
