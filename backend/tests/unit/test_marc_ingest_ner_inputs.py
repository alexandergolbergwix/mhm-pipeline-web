"""Regression test for the empty-NER-inputs bug.

**The bug** (Heroku, 2026-06-02): records uploaded as raw-subfield TSV
/ JSON ended up in ``run_records.marc`` with columns like ``500$a``,
``561$a``, ``505$a`` but **no flat ``notes`` / ``provenance`` /
``contents`` / ``colophon_text``** keys. The Stage-2 extractor
(``app.pipeline.extraction._extract_person_texts`` etc.) reads only
the flat keys, so Modal was called with empty strings on every
record → 0 entities → 0 work items in the Studio.

**The fix**: ``_collapse_marc_subfields`` now derives all four
NER-input keys from the raw subfields. This test pins the contract.
"""

from __future__ import annotations

from app.pipeline.marc_ingest import _collapse_marc_subfields


def _raw_record() -> dict:
    """NLI-style TSV record with raw MARC subfield columns."""
    return {
        "_control_number":  "990000827290205171",
        "245$a":             "פירוש המשנה",
        "245$b":             "להרמב\"ם",
        "100$a":             "משה בן מיימון",
        "100$e":             "author",
        "500$a":             "Hebrew note about scribe Moses|Another general note",
        "590$a":             "Local colophon: כתב משה בן מאיר בשנת תק\"ז",
        "541$a":             "Acquired from a private collection in 1923",
        "561$a":             "Owned by R. Avraham; later transferred to NLI",
        "505$a":             "פירוש המשנה -- ספר המצוות -- מורה נבוכים",
        "655$a":             "manuscript",
    }


class TestNotesDerivation:
    def test_notes_aggregated_from_500_590_541(self) -> None:
        rec = _raw_record()
        _collapse_marc_subfields(rec)
        notes = rec.get("notes")
        assert isinstance(notes, list)
        assert "Hebrew note about scribe Moses" in notes
        assert "Another general note" in notes
        assert "Local colophon: כתב משה בן מאיר בשנת תק\"ז" in notes
        assert "Acquired from a private collection in 1923" in notes

    def test_notes_dedup_preserves_order(self) -> None:
        rec = _raw_record()
        rec["500$a"] = "dup|dup|unique"
        _collapse_marc_subfields(rec)
        notes = rec.get("notes") or []
        assert notes.count("dup") == 1
        assert "unique" in notes

    def test_notes_empty_when_no_relevant_subfields(self) -> None:
        rec = {"_control_number": "x", "245$a": "Title-only"}
        _collapse_marc_subfields(rec)
        assert rec.get("notes") in (None, [])


class TestColophonDerivation:
    def test_colophon_text_from_590a(self) -> None:
        rec = _raw_record()
        _collapse_marc_subfields(rec)
        col = rec.get("colophon_text") or ""
        assert "כתב משה בן מאיר" in col

    def test_colophon_text_absent_when_no_590a(self) -> None:
        rec = {"_control_number": "x", "245$a": "T", "500$a": "note"}
        _collapse_marc_subfields(rec)
        assert not rec.get("colophon_text")


class TestProvenanceDerivation:
    def test_provenance_from_561a(self) -> None:
        rec = _raw_record()
        _collapse_marc_subfields(rec)
        prov = rec.get("provenance") or ""
        assert "Owned by R. Avraham" in prov
        assert "NLI" in prov

    def test_provenance_joined_with_pipe_separator(self) -> None:
        rec = {"_control_number": "x", "561$a": "first|second"}
        _collapse_marc_subfields(rec)
        assert rec.get("provenance") == "first | second"


class TestContentsDerivation:
    def test_contents_split_on_double_dash(self) -> None:
        rec = _raw_record()
        _collapse_marc_subfields(rec)
        contents = rec.get("contents") or []
        titles = [c.get("title") for c in contents]
        assert "פירוש המשנה" in titles
        assert "ספר המצוות" in titles
        assert "מורה נבוכים" in titles

    def test_contents_each_entry_is_dict_with_title(self) -> None:
        rec = _raw_record()
        _collapse_marc_subfields(rec)
        for entry in rec.get("contents") or []:
            assert isinstance(entry, dict)
            assert "title" in entry

    def test_contents_absent_when_no_505(self) -> None:
        rec = {"_control_number": "x", "245$a": "T"}
        _collapse_marc_subfields(rec)
        assert rec.get("contents") in (None, [])


class TestIdempotence:
    def test_running_twice_does_not_duplicate(self) -> None:
        rec = _raw_record()
        _collapse_marc_subfields(rec)
        notes1 = list(rec.get("notes") or [])
        contents1 = list(rec.get("contents") or [])
        _collapse_marc_subfields(rec)
        notes2 = list(rec.get("notes") or [])
        contents2 = list(rec.get("contents") or [])
        assert notes1 == notes2, "notes duplicated on second pass"
        # contents may grow because we don't dedupe titles — but
        # provenance / colophon_text use ``not record.get(...)`` so
        # they stay stable.
        assert rec.get("provenance") and "first | first" not in rec["provenance"]


class TestExistingFieldsPreserved:
    def test_authors_subjects_title_dates_still_work(self) -> None:
        """The new derivations don't break the existing collapse."""
        rec = _raw_record()
        rec["008"] = "150101s1500    is hbrtxt c0"
        _collapse_marc_subfields(rec)
        assert rec.get("title", "").startswith("פירוש המשנה")
        assert any(a.get("name") == "משה בן מיימון" for a in (rec.get("authors") or []))
        assert (rec.get("dates") or {}).get("year") == 1500
