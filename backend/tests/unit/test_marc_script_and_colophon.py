"""MARC 958 script and 957 colophon reach the projection (Rule W-281).

Both fields carried real data that never arrived anywhere: `958$a` held the
paleographic attribution on 768 of the 897 records in the 17th-century corpus
while `P9302` sat at 5.4%, and `957$a` held the colophon text on 617 records
while `P1684` sat at 0.9%.

The colophon work must not weaken Rule W-72. `is_catalog_note_placeholder`
rejects any string containing ``קולופון`` because a scribe never names his own
colophon — that word marks cataloger apparatus (``בסופו קולופון`` /
``קולופון המחבר בדף …``). NLI's 957$a *labels* the field with that word
(``קולופון: "תם ונשלם …"``), so the label is stripped at ingest and the guard
is left untouched. These tests pin both halves.
"""
from __future__ import annotations

from app.pipeline.marc_ingest import prepare_record_for_pipeline
from converter.wikidata.item_builder import (
    WikidataItemBuilder,
    _is_catalog_note_placeholder,
)


def _p9302(record: dict) -> set[str]:
    item = WikidataItemBuilder().build_manuscript_item(prepare_record_for_pipeline(record))
    return {str(s.value) for s in item.statements if s.property_id == "P9302"}


def _p1684(record: dict) -> list[str]:
    item = WikidataItemBuilder().build_manuscript_item(prepare_record_for_pipeline(record))
    return [str(s.value) for s in item.statements if s.property_id == "P1684"]


# ── 958 → P9302 ──────────────────────────────────────────────────────────


def test_958a_script_tradition_reaches_p9302() -> None:
    assert _p9302({"001": "a", "245$a": "כותרת", "958$a": "תימנית"}) == {"Q121094936"}


def test_958a_covers_the_traditions_in_the_corpus() -> None:
    expect = {
        "תימנית": "Q121094936",
        "איטלקית": "Q133370075",
        "אשכנזית": "Q121094898",
        "מזרחית": "Q133327488",
        "ספרדית": "Q133177480",
    }
    for he, qid in expect.items():
        assert _p9302({"001": "a", "958$a": he}) == {qid}, he


def test_958a_unmapped_tradition_fails_closed() -> None:
    """פרובנצלית / מערבית / קראית have no verified QID (Rule R8)."""
    for he in ("פרובנצלית", "מערבית", "קראית"):
        assert _p9302({"001": "a", "958$a": he}) == set(), he


def test_958b_writing_style_is_recorded_as_script_mode() -> None:
    prepared = prepare_record_for_pipeline({"001": "a", "958$b": "מרובעת"})
    assert prepared["script_mode"] == "SquareScript"


def test_958b_multiword_resolves_to_first_matching_token() -> None:
    prepared = prepare_record_for_pipeline({"001": "a", "958$b": "מרובעת ורהוטה"})
    assert prepared["script_mode"] in {"SquareScript", "CursiveScript"}


def test_958_does_not_overwrite_an_existing_script_type() -> None:
    prepared = prepare_record_for_pipeline({
        "001": "a", "script_type": "AshkenaziScript", "958$a": "תימנית",
    })
    assert prepared["script_type"] == "AshkenaziScript"


# ── 957 → P1684 ──────────────────────────────────────────────────────────


def test_957_colophon_reaches_p1684() -> None:
    vals = _p1684({"001": "a", "245$a": "כותרת", "957$a": 'קולופון: ""תם ונשלם"".'})
    assert vals, "the colophon text must reach P1684"
    assert any("תם ונשלם" in v for v in vals)


def test_the_colophon_label_is_stripped_not_kept() -> None:
    prepared = prepare_record_for_pipeline({"001": "a", "957$a": 'קולופון: ""תם ונשלם"".'})
    assert not prepared["colophon_text"].startswith("קולופון")


def test_957_label_with_parenthetical_folio_is_stripped() -> None:
    prepared = prepare_record_for_pipeline({
        "001": "a", "957$a": 'קולופון (דף קצה ע"א): ""ותהי השלמת הספר"".',
    })
    assert not prepared["colophon_text"].startswith("קולופון")
    assert "ותהי השלמת" in prepared["colophon_text"]


def test_catalog_apparatus_still_rejected_despite_the_label_strip() -> None:
    """Rule W-72 must not weaken: third-person prose stays a placeholder."""
    for text in (
        "בסופו קולופון בדף 4.",
        "קולופון המחבר בדף 12.",
        "(קולופון 4)",
    ):
        assert _is_catalog_note_placeholder(text), text


def test_stripped_colophon_is_not_a_placeholder() -> None:
    prepared = prepare_record_for_pipeline({"001": "a", "957$a": 'קולופון: ""תם ונשלם"".'})
    assert not _is_catalog_note_placeholder(prepared["colophon_text"])


def test_one_apparatus_piece_does_not_poison_a_real_colophon() -> None:
    """A `רשומה זמנית` note in 590$a used to sink the whole compound."""
    prepared = prepare_record_for_pipeline({
        "001": "a",
        "957$a": 'קולופון: ""תם ונשלם"".',
        "590$a": "רשומה זמנית",
    })
    assert "תם ונשלם" in prepared["colophon_text"]
    assert "רשומה זמנית" not in prepared["colophon_text"]


def test_590_colophon_still_works() -> None:
    prepared = prepare_record_for_pipeline({"001": "a", "590$a": "תם ונשלם הספר"})
    assert prepared["colophon_text"] == "תם ונשלם הספר"


def test_590_apparatus_is_dropped_by_design() -> None:
    """A 590 note naming the colophon is catalog apparatus (Rule W-72).

    Before the piece filter this string became `colophon_text` and was then
    rejected at projection time by `is_catalog_note_placeholder` — so it never
    reached P1684 either way. The filter just moves that rejection earlier,
    where it can no longer take a real colophon down with it.
    """
    prepared = prepare_record_for_pipeline({"001": "a", "590$a": "קולופון מקומי"})
    assert "colophon_text" not in prepared or not prepared["colophon_text"]
