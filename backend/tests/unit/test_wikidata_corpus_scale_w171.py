"""Corpus-scale / Rule W-171 regression tests (synthetic — no CN allowlists)."""

from __future__ import annotations

from pathlib import Path

import pytest

from converter.authority.heading_fidelity import identity_compatible
from converter.wikidata.catalog_notes import is_incipit_text
from converter.wikidata.isbd_title import split_isbd_title_subtitle
from converter.wikidata.work_link_specificity import (
    Q_BOOK_OF_ESTHER,
    Q_BIBLE,
    Q_TANAKH,
    refine_exemplar_work_qid,
    specific_biblical_work_qid,
)


class TestIsbdTitleSplit:
    def test_colon_in_245a_without_b(self) -> None:
        main, sub = split_isbd_title_subtitle("חלק א : תרגום ארמי", None)
        assert main == "חלק א"
        assert sub == "תרגום ארמי"

    def test_explicit_b_wins(self) -> None:
        main, sub = split_isbd_title_subtitle("Main title", "Subtitle here")
        assert main == "Main title"
        assert sub == "Subtitle here"

    def test_duplicate_full_string_splits(self) -> None:
        full = "Alpha : Beta remainder"
        main, sub = split_isbd_title_subtitle(full, full)
        assert main == "Alpha"
        assert sub == "Beta remainder"


class TestWorkLinkSpecificity:
    def test_esther_megillah_beats_tanakh(self) -> None:
        assert specific_biblical_work_qid("מגילת אסתר, קלף") == Q_BOOK_OF_ESTHER
        qid = refine_exemplar_work_qid(
            Q_TANAKH,
            title="מגילת אסתר",
            record={"notes": "מגילת אסתר על קלף"},
        )
        assert qid == Q_BOOK_OF_ESTHER

    def test_piyyut_blocks_broad_bible(self) -> None:
        qid = refine_exemplar_work_qid(
            Q_BIBLE,
            title="קובץ פיוטים",
            record={"notes": "פיוטים וסליחות; כולל פסוקים"},
        )
        assert qid is None

    def test_exact_bible_title_kept_without_piyyut(self) -> None:
        # Manuscript 245$a is Bible — keep. A bare RELATED_WORKS alias without
        # record support must not keep (see export-34 / W-173).
        qid = refine_exemplar_work_qid(
            Q_BIBLE, title="Bible", record={"title": "Bible"},
        )
        assert qid == Q_BIBLE

    def test_related_works_bible_alias_dropped_on_piyyut_ms(self) -> None:
        qid = refine_exemplar_work_qid(
            Q_BIBLE,
            title="Bible",
            record={"title": "פיוטים ושירים", "genres": ["Piyyutim"]},
        )
        assert qid is None


class TestIdentityCompatible:
    def test_same_family_different_given_incompatible(self) -> None:
        assert not identity_compatible("יוסף בן דוד אבהר", "סעדיה בן דוד אבהר")

    def test_matching_headings_compatible(self) -> None:
        assert identity_compatible("כהן, משה", "משה כהן")


class TestIncipitGate:
    def test_catalog_note_rejected(self) -> None:
        assert not is_incipit_text("רשומה זמנית | נושא נוסף: מכירה")

    def test_real_incipit_accepted(self) -> None:
        assert is_incipit_text("בראשית ברא אלהים")


class TestProductionYearCentury:
    def test_embedded_year_beats_century_midpoint(self) -> None:
        from converter.transformer.production_year import manuscript_production_year

        year = manuscript_production_year({
            "dates": {
                "date_format": "HebrewCentury",
                "year_start": 1501,
                "year_end": 1700,
                "original_string": 'מאה ט"ז-י"ז, לפני תל"ו (1676)',
            },
        })
        assert year == 1676


class TestManuscriptProjectionHygiene:
    def test_isbd_split_emits_distinct_p1476_p1680(self) -> None:
        from converter.wikidata.item_builder import WikidataItemBuilder

        builder = WikidataItemBuilder(reconciler=None)
        item = builder.build_manuscript_item({
            "_control_number": "SYNTH-ISBD-1",
            "title": "חלק ראשון : והוא פירוש",
            "subtitle": "חלק ראשון : והוא פירוש",
            "shelfmark": "Heb. 8° 1",
        })
        titles = [
            str(s.value) for s in item.statements if s.property_id == "P1476"
        ]
        subs = [
            str(s.value) for s in item.statements if s.property_id == "P1680"
        ]
        assert titles
        assert subs
        assert titles[0] != subs[0]
        assert ":" not in titles[0] or titles[0].count(":") < item.labels.get("he", "x").count(":")

    def test_leaf_extent_attaches_leaf_unit(self) -> None:
        from converter.wikidata.item_builder import WikidataItemBuilder
        from converter.wikidata.property_mapping import Q_LEAF_UNIT

        builder = WikidataItemBuilder(reconciler=None)
        item = builder.build_manuscript_item({
            "_control_number": "SYNTH-LEAF-1",
            "title": "סידור",
            "extent": "279 דף",
            "extent_unit": "leaf",
            "shelfmark": "F 1",
        })
        p1104 = [s for s in item.statements if s.property_id == "P1104"]
        assert p1104
        assert int(p1104[0].value) == 279
        assert p1104[0].unit == Q_LEAF_UNIT

    def test_work_title_stripped_from_aliases(self) -> None:
        from converter.wikidata.item_builder import WikidataItemBuilder

        builder = WikidataItemBuilder(reconciler=None)
        item = builder.build_manuscript_item({
            "_control_number": "SYNTH-ALIAS-1",
            "title": "קובץ",
            "shelfmark": "Heb. 1",
            "variant_titles": ["נר לרגלי"],
            "related_works": [{"title": "נר לרגלי", "approved": True}],
        })
        he_aliases = [a.casefold() for a in (item.aliases.get("he") or [])]
        assert "נר לרגלי" not in he_aliases

    def test_catalog_note_not_emitted_as_p1922(self) -> None:
        from converter.wikidata.item_builder import WikidataItemBuilder

        builder = WikidataItemBuilder(reconciler=None)
        item = builder.build_manuscript_item({
            "_control_number": "SYNTH-INCIPIT-1",
            "title": "חיבור",
            "shelfmark": "Heb. 2",
            "has_incipit": "רשומה זמנית",
        })
        assert not any(s.property_id == "P1922" for s in item.statements)

    def test_scribe_public_qid_withheld_on_heading_mismatch(self) -> None:
        from converter.wikidata.item_builder import WikidataItemBuilder
        from converter.wikidata.item_models import WikidataItem

        builder = WikidataItemBuilder(reconciler=None)
        # Seed a person that will be created with mismatch via preferred name.
        record = {
            "_control_number": "SYNTH-SCRIBE-1",
            "title": "כתב יד",
            "shelfmark": "Heb. 3",
            "marc_authority_matches": [{
                "name": "יהודה בן יצחק דדיניא",
                "role": "מעתיק",
                "mazal_id": "987007299516905171",
                "preferred_name_heb": "יצחק בן יהודה הלוי",
                "wikidata_qid": "Q118161349",
            }],
        }
        item = builder.build_manuscript_item(record)
        p11603 = [
            s for s in item.statements
            if s.property_id == "P11603"
        ]
        for stmt in p11603:
            assert str(stmt.value) != "Q118161349"


class TestStickyFull:
    def test_sticky_full_matches_claims_fingerprint(self) -> None:
        from app.pipeline.wikidata_verdict_cache import (
            cached_verdict_is_sticky_full,
            wikidata_claims_fingerprint,
        )

        item = {
            "local_id": "SYNTH-V-1",
            "entity_type": "manuscript",
            "labels": {"en": "Test"},
            "statements": [
                {"property_id": "P31", "value": "Q87167", "value_type": "item"},
            ],
        }
        claims = wikidata_claims_fingerprint(item, "gemini-3.5-flash")
        cached = {
            "overall": "full",
            "reasoning": "ok",
            "model": "gemini-3.5-flash",
            "evaluator": "wikidata_item",
            "cache_key": "old-schema-key",
            "claims_fingerprint": claims,
        }
        assert cached_verdict_is_sticky_full(
            cached, item,
            judge_model="gemini-3.5-flash",
            evaluator_id="wikidata_item",
        )

    def test_sticky_full_rejects_partial(self) -> None:
        from app.pipeline.wikidata_verdict_cache import cached_verdict_is_sticky_full

        item = {"local_id": "SYNTH-V-2", "entity_type": "manuscript", "labels": {}}
        cached = {
            "overall": "partial",
            "reasoning": "bad claim",
            "model": "gemini-3.5-flash",
            "cache_key": "x",
            "claims_fingerprint": "y",
        }
        assert not cached_verdict_is_sticky_full(
            cached, item,
            judge_model="gemini-3.5-flash",
            evaluator_id="wikidata_item",
        )

    def test_claim_change_breaks_sticky(self) -> None:
        from app.pipeline.wikidata_verdict_cache import (
            cached_verdict_is_sticky_full,
            wikidata_claims_fingerprint,
        )

        item = {
            "local_id": "SYNTH-V-3",
            "entity_type": "manuscript",
            "labels": {"en": "A"},
            "statements": [{"property_id": "P31", "value": "Q87167"}],
        }
        claims = wikidata_claims_fingerprint(item, "gemini-3.5-flash")
        item["statements"] = [{"property_id": "P31", "value": "Q571"}]
        cached = {
            "overall": "full",
            "reasoning": "ok",
            "model": "gemini-3.5-flash",
            "evaluator": "wikidata_item",
            "cache_key": "stale",
            "claims_fingerprint": claims,
        }
        assert not cached_verdict_is_sticky_full(
            cached, item,
            judge_model="gemini-3.5-flash",
            evaluator_id="wikidata_item",
        )


class TestRubricHygiene:
    def test_rubric_has_mode_beta_clauses(self) -> None:
        root = Path(__file__).resolve().parents[3]
        rubric = (
            root / "eval-agent" / "config" / "rubrics" / "wikidata_item.md"
        ).read_text(encoding="utf-8")
        assert "Absent claims are never defects" in rubric
        assert "Trust `value_label`" in rubric or "Trust `value_label`" in rubric.replace("`", "")
        assert "Facsimile" in rubric or "facsimile" in rubric
        assert '"full"' in rubric


class TestCorpusTsvOptional:
    @pytest.mark.skipif(
        not Path(
            "/Users/alexandergo/Documents/Doctorat/pipeline/data/tsvs/"
            "filtered_manuscripts_after_906a.tsv"
        ).is_file(),
        reason="desktop corpus TSV not present",
    )
    def test_stream_sample_has_expected_columns(self) -> None:
        import csv

        path = Path(
            "/Users/alexandergo/Documents/Doctorat/pipeline/data/tsvs/"
            "filtered_manuscripts_after_906a.tsv"
        )
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            assert reader.fieldnames
            assert "245$a" in reader.fieldnames
            assert "300$a" in reader.fieldnames
            colon = 0
            leaf = 0
            n = 0
            for row in reader:
                if n >= 5000:
                    break
                n += 1
                a = row.get("245$a") or ""
                if ":" in a:
                    colon += 1
                extent = row.get("300$a") or ""
                if any(tok in extent for tok in ("דף", "leaf", "folio", "ff.")):
                    leaf += 1
            assert n == 5000
            assert colon > 0
            assert leaf > 0
