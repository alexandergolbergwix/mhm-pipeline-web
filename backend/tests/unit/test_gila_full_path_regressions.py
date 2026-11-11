"""Source-derived regressions for the full Gila repair plan."""

import pytest
from app.pipeline.marc_ingest import prepare_record_for_pipeline
from app.pipeline.wikidata_canonical_enrichment import merge_legacy_into_canonical
from converter.wikidata.item_builder import WikidataItemBuilder
from converter.wikidata.item_models import WikidataItem, WikidataStatement


def _claims(item: WikidataItem, property_id: str) -> list[WikidataStatement]:
    return [claim for claim in item.statements if claim.property_id == property_id]


def test_structured_occurrences_keep_different_authors_at_the_same_folio() -> None:
    record = {
        "_control_number": "990001",
        "title": "קובץ",
        "contents": [
            {"title": "ספר הדרושים", "author": author, "folio_range": "א-ב", "source_field": "505"}
            for author in ("יצחק חיים קנטריני", "אברהם הלוי")
        ],
    }
    items = WikidataItemBuilder().build_all([record])
    manuscript = next(item for item in items if item.entity_type == "manuscript")
    assert len(_claims(manuscript, "P1574")) == 2
    assert len([item for item in items if item.entity_type == "work"]) == 2


def test_ner_occurrences_keep_repeated_work_with_distinct_folios() -> None:
    record = {
        "_control_number": "990001",
        "title": "קובץ",
        "entities": [
            {"type": "FOLIO", "source": "contents_ner", "text": "א-ב", "start": 5, "end": 9},
            {
                "type": "WORK",
                "source": "contents_ner",
                "text": "ספר הדרושים",
                "start": 10,
                "end": 23,
            },
            {"type": "FOLIO", "source": "contents_ner", "text": "ג-ד", "start": 55, "end": 59},
            {
                "type": "WORK",
                "source": "contents_ner",
                "text": "ספר הדרושים",
                "start": 60,
                "end": 73,
            },
        ],
    }
    items = WikidataItemBuilder().build_all([record])
    manuscript = next(item for item in items if item.entity_type == "manuscript")
    claims = _claims(manuscript, "P1574")
    assert {
        q["value"] for claim in claims for q in claim.qualifiers if q["property"] == "P958"
    } == {"א-ב", "ג-ד"}
    assert len({str(claim.value) for claim in claims}) == 1


def test_ner_folio_reuses_the_source_work_qid_after_safe_author_fallback() -> None:
    item = WikidataItemBuilder().build_manuscript_item(
        {
            "_control_number": "990001",
            "title": "ספר הדרושים",
            "authors": [{"name": "מחבר ראשון"}],
            "contents": [{"title": "ספר הדרושים", "author": "מחבר ראשון", "wikidata_qid": "Q123"}],
            "entities": [
                {"type": "FOLIO", "source": "contents_ner", "text": "א-ב", "start": 0, "end": 3},
                {
                    "type": "WORK",
                    "source": "contents_ner",
                    "text": "ספר הדרושים",
                    "start": 4,
                    "end": 16,
                },
            ],
        }
    )
    claims = _claims(item, "P1574")
    assert len(claims) == 2
    assert {claim.value for claim in claims} == {"Q123"}


def test_one_approved_work_qid_does_not_override_a_different_source_author() -> None:
    record = {
        "_control_number": "990001",
        "title": "קובץ",
        "contents": [
            {"title": "ספר הדרושים", "author": author, "folio_range": folio, "source_field": "505"}
            for author, folio in (("יצחק חיים קנטריני", "א-ב"), ("אברהם הלוי", "ג-ד"))
        ],
        "marc_authority_matches": [
            {
                "name": "ספר הדרושים",
                "role": "work",
                "approved": True,
                "wikidata_qid": "Q141755239",
                "author_name": "יצחק חיים קנטריני",
            }
        ],
    }
    items = WikidataItemBuilder().build_all([record])
    manuscript = next(item for item in items if item.entity_type == "manuscript")
    abraham = next(
        claim
        for claim in _claims(manuscript, "P1574")
        if any(q.get("property") == "P958" and q.get("value") == "ג-ד" for q in claim.qualifiers)
    )
    assert abraham.value != "Q141755239"


def test_same_title_component_does_not_suppress_the_main_work_by_another_author() -> None:
    record = {
        "_control_number": "990001",
        "title": "ספר הדרושים",
        "authors": [{"name": "יצחק חיים קנטריני"}],
        "contents": [
            {
                "title": "ספר הדרושים",
                "author": "אברהם הלוי",
                "source_field": "500",
                "candidate_kind": "named_work",
            }
        ],
    }
    items = WikidataItemBuilder().build_all([record])
    authors = {
        str(claim.value)
        for item in items
        if item.entity_type == "work"
        for claim in _claims(item, "P2093")
    }
    assert authors == {"יצחק חיים קנטריני", "אברהם הלוי"}


def _work(local_id: str, author: str) -> WikidataItem:
    return WikidataItem(
        local_id=local_id,
        entity_type="work",
        labels={"he": "דרושים"},
        records=["990001"],
        statements=[
            WikidataStatement(property_id="P31", value="Q47461344", value_type="item"),
            WikidataStatement(property_id="P2093", value=author, value_type="string"),
        ],
    )


def test_canonical_merge_does_not_reuse_one_work_for_conflicting_authors() -> None:
    canonical = [_work("cantarini", "יצחק חיים קנטריני"), _work("abraham", "אברהם הלוי")]
    legacy = _work("legacy-cantarini", "יצחק חיים קנטריני")
    merged = merge_legacy_into_canonical(canonical, [legacy])
    abraham = next(item for item in merged if item.local_id == "abraham")
    assert {claim.value for claim in _claims(abraham, "P2093")} == {"אברהם הלוי"}


def test_canonical_merge_does_not_erase_a_conflicting_public_work_qid() -> None:
    canonical = _work("canonical", "יצחק חיים קנטריני")
    legacy = _work("legacy", "יצחק חיים קנטריני")
    canonical.existing_qid = "Q100"
    legacy.existing_qid = "Q200"
    merged = merge_legacy_into_canonical([canonical], [legacy])
    assert {item.existing_qid for item in merged} == {"Q100", "Q200"}


def test_a_named_commentary_is_not_catalogue_noise_because_it_mentions_ketuvim() -> None:
    items = WikidataItemBuilder().build_all(
        [
            {
                "_control_number": "990001",
                "title": "פרוש כתובים ליפת בן עלי (איוב)",
                "authors": [{"name": "יפת בן עלי"}],
            }
        ]
    )
    manuscript = next(item for item in items if item.entity_type == "manuscript")
    assert _claims(manuscript, "P1574")
    assert any(item.entity_type == "work" for item in items)


def test_an_unresolved_short_supplement_has_an_explicit_projection_reason() -> None:
    item = WikidataItemBuilder().build_manuscript_item(
        {
            "_control_number": "990001",
            "title": "קובץ",
            "contents": [
                {"title": "ספר הדרושים", "wikidata_qid": "Q123", "source_field": "505"},
                {"title": "דרוש", "folio_range": "ג-ד", "source_field": "505"},
            ],
        }
    )
    evidence = next(row for row in item.work_candidate_evidence if row.get("title") == "דרוש")
    assert evidence["projection_status"] == "deferred"
    assert evidence["projection_reason"] == "unknown_text_blocked_by_existing_exemplar"


@pytest.mark.asyncio
async def test_a_later_specific_work_retains_the_first_unknown_occurrence_as_deferred() -> None:
    from app.pipeline.wikidata_studio import build_items_for_run

    result = await build_items_for_run(
        marc_records=[
            {
                "_control_number": "990001",
                "title": "קובץ",
                "500$a": "כולל: תקונים, ומדרש הנעלם לבראשית",
            }
        ],
        approved_matches=[],
        return_native=True,
    )
    item = next(item for item in result["native_items"] if item.entity_type == "manuscript")
    evidence = next(row for row in item.work_candidate_evidence if row.get("title") == "תקונים")
    assert evidence["projection_status"] == "deferred"
    assert evidence["projection_reason"] == "unknown_text_superseded_by_specific_exemplar"


@pytest.mark.parametrize("title", ["נר ה'", "מאמר שבתות ה'", 'אגרת בענין תואר ""מעלת החכם""'])
def test_source_title_punctuation_survives_the_public_qualifier(title: str) -> None:
    record = prepare_record_for_pipeline({"001": "990001", "245$a": "קובץ", "505$a": title})
    item = WikidataItemBuilder().build_manuscript_item(record)
    assert any(
        q.get("property") == "P1932" and q.get("value") == title.replace('""', '"')
        for claim in _claims(item, "P1574")
        for q in claim.qualifiers
    )


def test_a_citation_abbreviation_inside_a_title_does_not_reject_the_work() -> None:
    item = WikidataItemBuilder().build_manuscript_item(
        {
            "_control_number": "990001",
            "title": "אלפסי זוטא",
            "authors": [{"name": "מנחם עזריה"}],
        }
    )
    assert _claims(item, "P1574")


def test_latin_main_work_keeps_its_source_author() -> None:
    items = WikidataItemBuilder().build_all(
        [
            {
                "_control_number": "990001",
                "title": "Conselhos de ética",
                "authors": [{"name": "Isaac"}],
            }
        ]
    )
    assert any(
        item.entity_type == "work" and item.labels.get("en") == "Conselhos de ética"
        for item in items
    )


@pytest.mark.parametrize(
    "note",
    [
        'כולל מקומות שבו בוצעה ההלוואה, בין השאר: "מוראנו", "ויללא נווא"',
        'כולל עדות על "ארבעה וחמשה אנשים" בעיר "מיקולוניס"',
        'כולל כמה ציורים, ציור גדול וצבעוני בערך "בר שניא"',
        'דף 1א-10ב: חדושים מסודרים בסדר אלף בית, מגיע עד "ערך משה".',
        'דף 1א-10ב: חדושים מסודרים בסדר אלף בית, מגיע עד ""ערך משה"".',
    ],
)
def test_non_work_narrative_retains_evidence_without_false_work_candidates(note: str) -> None:
    record = prepare_record_for_pipeline({"001": "990001", "245$a": "קובץ", "500$a": note})
    assert not record.get("work_mentions")
    assert record["non_work_content_evidence"]
    assert note in str(record.get("notes"))


def test_specific_script_wording_survives_the_complete_builder() -> None:
    record = prepare_record_for_pipeline(
        {
            "001": "990000802880205171",
            "245$a": "אגרת סוכה",
            "958$a": "קראית-אשכנזית",
        }
    )
    manuscript = WikidataItemBuilder().build_manuscript_item(record)
    script = next(claim for claim in _claims(manuscript, "P9302") if claim.value == "Q121094898")
    assert any(
        q.get("property") == "P1932" and q.get("value") == "קראית-אשכנזית"
        for q in script.qualifiers
    )


def test_prose_bibliography_is_not_a_catalogue_code() -> None:
    record = prepare_record_for_pipeline(
        {
            "001": "990001915930205171",
            "245$a": "דרושים",
            "581$a": "Weisz, Katalog der Handschriften, Budapest, 1906, pp. 123-124.",
        }
    )
    manuscript = WikidataItemBuilder().build_manuscript_item(record)
    assert not _claims(manuscript, "P528")
    assert record.get("catalog_references")


def test_a_related_place_does_not_prove_manuscript_attestation() -> None:
    record = prepare_record_for_pipeline(
        {
            "001": "990000885680205171",
            "245$a": "חזוק אמונה",
            "751$a": "Buchach (Ukraine)",
            "751$e": "related place",
        }
    )
    item = WikidataItemBuilder().build_manuscript_item(record)
    assert not _claims(item, "P7153")
    assert record.get("related_places")


def test_letter_genre_and_uncertain_person_scope_stay_off_the_whole_manuscript() -> None:
    record = {
        "_control_number": "990001",
        "title": "חזוק אמונה",
        "genres": ["Letters"],
        "contents": [
            {
                "title": "העתק מן הכתב ששלח ר' אלי' בחור המדקדק לאיש נוצרי",
                "folio_range": "51א-52א",
                "source_field": "500",
                "candidate_kind": "named_work",
            }
        ],
        "contributors": [
            {
                "name": "אליהו בן אשר הלוי",
                "role": "copied from",
                "field": "700",
                "mazal_id": "987007000000000001",
            }
        ],
    }
    items = WikidataItemBuilder().build_all([record])
    manuscript = next(item for item in items if item.entity_type == "manuscript")
    assert not _claims(manuscript, "P136")
    assert not _claims(manuscript, "P3342")
    assert any(
        row.get("reason") == "person_role_scope_unresolved"
        for row in manuscript.work_candidate_evidence
    )
    letter = next(item for item in items if item.entity_type == "work")
    assert any(claim.value == "Q133492" for claim in _claims(letter, "P136"))
