"""Rule W-164 — designation labels, complete glosses, audited inception.

Three defects from run 48ba6c13, all in what a manuscript item asserts about
itself:

* 64 of 68 items carried the MARC 245 in `labels.he` — the title of a text the
  manuscript contains, not a designation for the physical carrier.
* A P195 on Q1256981 rendered `value_label: null` even though the verified label
  sat in `holding_institutions`.
* Two items asserted an inception of 1501 while their own MARC 260 $c read 1676:
  `date_to_wikidata` encodes a century as its first year and the audited
  production-year policy was not on the P571 path.
"""

from __future__ import annotations

from converter.wikidata.property_labels import qid_label


class TestValueLabelCompleteness:
    def test_a_holder_qid_resolves_through_the_audited_table(self) -> None:
        """The exact null-gloss from the export."""
        assert qid_label("Q1256981") == "San Francisco State University"

    def test_qid_labels_still_wins_when_it_has_the_gloss(self) -> None:
        assert qid_label("Q87167") == "manuscript"

    def test_an_unknown_qid_still_falls_back_to_itself(self) -> None:
        assert qid_label("Q999999999") == "Q999999999"

    def test_exodus_is_no_longer_glossed_as_jews(self) -> None:
        """A wrong gloss also made a P921 filter drop Exodus as "too broad"."""
        from app.pipeline.hmo_canonical_wikidata import _BROAD_MAIN_SUBJECT_QIDS

        assert qid_label("Q9190") == "Exodus"
        assert "Q9190" not in _BROAD_MAIN_SUBJECT_QIDS


class TestVerifiedStaticQids:
    """Rule W-26 — a QID written from memory looks exactly like a verified one."""

    def test_the_talmud_tractates_are_tractates(self) -> None:
        from converter.wikidata.property_mapping import TALMUD_TRACTATE_TO_QID

        # Every one of these 14 pointed somewhere else entirely: Shabbat at a
        # species of arachnid, Bava Batra at the central bank of Brazil.
        assert TALMUD_TRACTATE_TO_QID["שבת"] == "Q2703125"
        assert TALMUD_TRACTATE_TO_QID["עבודה זרה"] == "Q791251"
        assert TALMUD_TRACTATE_TO_QID["בבא בתרא"] == "Q811988"
        assert qid_label("Q791251") == "Avodah Zarah"

    def test_the_bible_books_are_bible_books(self) -> None:
        from converter.wikidata.property_mapping import BIBLE_BOOK_TO_QID

        assert BIBLE_BOOK_TO_QID["Jeremiah"] == "Q131590"   # was Tom and Jerry
        assert BIBLE_BOOK_TO_QID["Leviticus"] == "Q41490"   # was calcium carbonate
        assert BIBLE_BOOK_TO_QID["Isaiah"] == "Q131458"     # was Yggdrasil
        assert qid_label("Q131590") == "Jeremiah"

    def test_no_projected_subject_qid_shares_a_value_with_another(self) -> None:
        from converter.wikidata.property_mapping import (
            BIBLE_BOOK_TO_QID,
            TALMUD_TRACTATE_TO_QID,
        )

        for table in (BIBLE_BOOK_TO_QID, TALMUD_TRACTATE_TO_QID):
            assert len(set(table.values())) == len(table)
        assert not (set(BIBLE_BOOK_TO_QID.values()) & set(TALMUD_TRACTATE_TO_QID.values()))


class TestInceptionUsesTheAuditedYear:
    @staticmethod
    def _record(**extra) -> dict:
        record = {
            "_control_number": "990000825080205171",
            "control_number": "990000825080205171",
            "title": "ספר",
            "shelfmark": "F 1",
            # The real 260 $c from the two affected records: a 16th-17th century
            # range with the colophon year spelled out in the same field.
            "dates": {
                "original_string": 'מאה ט"ז-י"ז, לפני תל"ו (1676)',
                "format": "HebrewCentury",
            },
        }
        record.update(extra)
        return record

    def _inception(self, record: dict) -> tuple[str, int] | None:
        from converter.wikidata.item_builder import WikidataItemBuilder

        item = WikidataItemBuilder().build_manuscript_item(record)
        for stmt in item.statements:
            if stmt.property_id == "P571":
                return stmt.value, stmt.precision
        return None

    def test_a_colophon_year_narrows_a_century(self) -> None:
        found = self._inception(self._record(colophon_year=1676))
        assert found is not None
        value, precision = found
        assert value.startswith("+1676")
        assert precision == 9  # year, not century

    def test_a_century_with_no_colophon_stays_a_century(self) -> None:
        found = self._inception(self._record(
            dates={"original_string": 'מאה ט"ז', "date_format": "HebrewCentury"},
        ))
        assert found is not None
        value, precision = found
        assert value.startswith("+1501")
        assert precision == 7  # century — honest about what the catalogue said

    def test_a_colophon_year_outside_the_catalog_range_is_ignored(self) -> None:
        # 1250 is not inside the 16th century the catalogue committed to, so the
        # century stands: a colophon narrows a range, it never overrides one.
        found = self._inception(self._record(
            dates={"original_string": 'מאה ט"ז', "date_format": "HebrewCentury"},
            colophon_year=1250,
        ))
        assert found is not None
        assert found[0].startswith("+1501")
        assert found[1] == 7


class TestTheBraginskyItemEndToEnd:
    """The exact item from run 48ba6c13 that shipped labelled "Jerusalem, NLI"."""

    _CN = "990001882630205171"

    def _labels(self) -> tuple[dict, dict]:
        from app.pipeline.hmo_canonical import CanonicalHmoEntity
        from app.pipeline.hmo_canonical_wikidata import (
            _wikidata_labels_and_aliases,
            canonical_studio_context,
        )

        record = {
            "_control_number": self._CN,
            "control_number": self._CN,
            "title": "מלאכת שלמה (סדר זרעים)",
            "shelfmark": "F 41164",
            "languages": ["heb"],
            "contributors": [{
                "name": '"Braginsky Collection of Hebrew Manuscripts and Printed Books',
                "role": '"current owner',
                "field": "710",
            }],
        }
        entity = CanonicalHmoEntity(
            local_id=f"QDraft_MS_{self._CN}",
            source_uri=f"https://example.org/marc/{self._CN}",
            wikibase_id="Q1",
            revision_id=1,
            labels={"he": "מלאכת שלמה (סדר זרעים)"},
            descriptions={},
            aliases={},
            claims=[{
                "property_uri": "https://w3id.org/mhm/ontology#shelfmark",
                "value": "F 41164",
            }],
            authority_evidence=[],
            source_fingerprint="fp",
            entity_type="manuscript",
            control_numbers=[self._CN],
        )
        context = canonical_studio_context(marc_records=[record], approved_matches=[])
        return _wikidata_labels_and_aliases(entity, "manuscript", context=context)

    def test_the_label_names_the_holder_marc_attests(self) -> None:
        labels, _aliases = self._labels()
        assert labels["en"] == "Braginsky Collection, F 41164"
        assert "NLI" not in labels["en"]

    def test_the_hebrew_label_is_a_designation_and_the_title_an_alias(self) -> None:
        labels, aliases = self._labels()
        assert labels["he"] == "כתב יד עברי, Braginsky Collection, F 41164"
        assert "מלאכת שלמה (סדר זרעים)" in aliases["he"]
