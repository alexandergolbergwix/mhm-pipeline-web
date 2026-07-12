"""Source-aware Wikidata work-candidate regression tests."""

from converter.wikidata.work_candidates import assess_work_candidate


def test_clean_hebrew_505_title_is_accepted() -> None:
    decision = assess_work_candidate(
        "ספר הדרושים",
        source_field="505",
        candidate_kind="named_work",
        folio_range="א-ב",
        sequence=1,
    )
    assert decision.accepted
    assert decision.reason == "named_work_in_505"
    assert decision.folio_range == "א-ב"
    assert decision.sequence == 1


def test_structured_500_named_work_is_accepted() -> None:
    decision = assess_work_candidate(
        "כוונות התפילה לכל השנה",
        source_field="500",
        candidate_kind="named_work",
    )
    assert decision.accepted
    assert decision.reason == "named_work_in_500"


def test_unstructured_500_fragment_is_rejected() -> None:
    decision = assess_work_candidate("מועדים", source_field="500")
    assert not decision.accepted
    assert decision.reason == "unstructured_500_note"


def test_bibliographic_citation_is_rejected() -> None:
    decision = assess_work_candidate(
        "תשובות הרמבם, מהדורת פריימאן, ירושלים, סי' סז",
        source_field="500",
        candidate_kind="named_work",
    )
    assert not decision.accepted
    assert decision.reason == "bibliographic_fragment"


def test_latin_505_heading_requires_authority() -> None:
    decision = assess_work_candidate("Diodati Segre", source_field="505")
    assert not decision.accepted
    assert decision.reason == "latin_title_requires_authority"


def test_latin_title_with_known_qid_is_accepted() -> None:
    decision = assess_work_candidate(
        "Diodati Segre",
        source_field="505",
        known_qid="Q123",
    )
    assert decision.accepted
    assert decision.reason == "known_wikidata_work"


def test_unbalanced_parenthesis_is_removed_from_title() -> None:
    decision = assess_work_candidate("ספר הכונות (קטע", source_field="505")
    assert decision.accepted
    assert decision.title == "ספר הכונות"


def test_catalogue_prose_and_dedications_are_rejected() -> None:
    bad_titles = [
        "רק דפים אחדים מסוף החבור",
        "פחות מבנדפס",
        "מערב אירופה, מרכזה ומזרחה - פולין",
        "לכבוד מעלת השר החשמן מבריציו משבויה",
        'הר"ר יהודה סיני יצ"ו',
    ]
    for title in bad_titles:
        decision = assess_work_candidate(
            title,
            source_field="500",
            candidate_kind="named_work",
        )
        assert not decision.accepted, title
        assert decision.reason == "catalogue_prose"
