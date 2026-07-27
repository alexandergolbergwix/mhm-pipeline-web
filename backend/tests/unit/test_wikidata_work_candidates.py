"""Source-aware Wikidata work-candidate regression tests."""

from converter.wikidata.work_candidates import assess_work_candidate


def test_marc_245_title_is_accepted_as_main_work_evidence() -> None:
    decision = assess_work_candidate(
        "סדור מנהג קרפנטרץ לראש השנה",
        source_field="245",
        candidate_kind="marc_245_title",
    )
    assert decision.accepted
    assert decision.reason == "marc_245_title"


def test_marc_100_245_title_author_is_accepted() -> None:
    decision = assess_work_candidate(
        "אב הרחמים",
        source_field="100/245",
        candidate_kind="marc_title_author",
    )
    assert decision.accepted
    assert decision.reason == "marc_title_author"


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


def test_doubled_marc_quotes_preserve_hebrew_abbreviation_marks() -> None:
    for raw, expected in (
        ('""תרגום רס""ג לתורה""', 'תרגום רס"ג לתורה'),
        ('""פרוש רש""י""', 'פרוש רש"י'),
        ('""תשב""ץ""', 'תשב"ץ'),
        ('""מאמרי חז""ל""', 'מאמרי חז"ל'),
    ):
        decision = assess_work_candidate(raw, source_field="500", candidate_kind="named_work")
        assert decision.title == expected
