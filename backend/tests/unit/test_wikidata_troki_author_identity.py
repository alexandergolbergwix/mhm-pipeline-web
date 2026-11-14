"""The exact Troki authority proof cannot override a rejected identity."""

from app.pipeline.marc_ingest import prepare_record_for_pipeline
from converter.wikidata.item_builder import WikidataItemBuilder
from converter.wikidata.person_projection import audited_person_authority


def test_the_exact_source_author_uses_the_verified_troki_authority() -> None:
    record = prepare_record_for_pipeline(
        {
            "001": "990000885680205171",
            "245$a": "חזוק אמונה",
            "100$a": "יצחק בן אברהם,",
            "100$c": "מטרוקי,",
            "100$d": "1533-1594",
        }
    )
    work = next(
        item for item in WikidataItemBuilder().build_all([record]) if item.entity_type == "work"
    )
    assert [s.value for s in work.statements if s.property_id == "P50"] == ["Q168171"]
    authority = audited_person_authority(record["authors"][0]["name"], source_record=record)
    assert authority is not None
    assert authority["mazal_id"] == "987007297464205171"
    assert authority["viaf_uri"] == "https://viaf.org/viaf/67288238"


def test_a_short_shared_patronymic_does_not_select_troki() -> None:
    assert audited_person_authority("יצחק בן אברהם") is None


def test_the_audited_fallback_requires_the_same_complete_source_dates() -> None:
    for dates in ["", "1600-1670", "1533?-1594"]:
        source = {"authors": [{"name": "יצחק בן אברהם מטרוקי", "dates": dates}]}
        assert audited_person_authority("יצחק בן אברהם מטרוקי", source_record=source) is None


def test_an_explicit_rejected_match_still_blocks_the_audited_troki_fallback() -> None:
    record = prepare_record_for_pipeline(
        {
            "001": "990000885680205171",
            "245$a": "חזוק אמונה",
            "100$a": "יצחק בן אברהם,",
            "100$c": "מטרוקי,",
            "100$d": "1533-1594",
        }
    )
    record["marc_authority_matches"] = [
        {
            "name": "יצחק בן אברהם מטרוקי",
            "role": "author",
            "approved": False,
            "wikidata_qid": "Q168171",
        }
    ]
    work = next(
        item for item in WikidataItemBuilder().build_all([record]) if item.entity_type == "work"
    )
    assert not [s for s in work.statements if s.property_id == "P50"]
    assert [s.value for s in work.statements if s.property_id == "P2093"] == [
        "יצחק בן אברהם מטרוקי"
    ]
