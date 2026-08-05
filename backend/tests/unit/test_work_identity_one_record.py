"""Rule W-165 — a work item is attested from exactly one record.

QDraft_Work_37 shipped three answers to "which record is this work from?": a
label taken from the HMO snapshot (מחזור מנהג אשכנז המערבי), a description citing
record 990000592310205171 (whose MARC 245 is גלא עמיקתא — a different work
entirely), and work_candidate_evidence sourced from a third record,
990001253400205171. Manuscripts have had an identity anchor since Rule W-137;
works had none, and three independent walks each picked their own record.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.pipeline.hmo_canonical_wikidata import _titles_match


class TestTitleMatching:
    def test_a_short_word_no_longer_matches_a_long_title(self) -> None:
        """The mechanism that bound QDraft_Work_37 to the wrong record."""
        assert not _titles_match("מחזור", "מחזור מנהג אשכנז המערבי (וורמיזא) לכל השנה")

    def test_an_exact_title_still_matches(self) -> None:
        assert _titles_match("משנה תורה", "משנה תורה")

    def test_sanitisation_and_punctuation_are_ignored(self) -> None:
        assert _titles_match('"גלא עמיקתא (חלק א, ב)."', "גלא עמיקתא (חלק א, ב)")

    def test_an_isbd_subtitle_break_still_matches_its_stem(self) -> None:
        assert _titles_match(
            "שער שברי לוחות : פירוש המסורת", "שער שברי לוחות",
        )

    def test_loose_mode_keeps_substring_matching_for_discovery(self) -> None:
        assert _titles_match(
            "מחזור מנהג אשכנז", "מחזור מנהג אשכנז המערבי לכל השנה", mode="loose",
        )

    def test_loose_mode_still_refuses_a_single_short_word(self) -> None:
        assert not _titles_match(
            "מחזור", "מחזור מנהג אשכנז המערבי לכל השנה", mode="loose",
        )

    def test_strict_is_the_default(self) -> None:
        assert not _titles_match("מחזור מנהג", "מחזור מנהג אשכנז המערבי לכל השנה")


@dataclass
class _Item:
    local_id: str = "QDraft_Work_37"
    entity_type: str = "work"
    labels: dict = field(default_factory=lambda: {"he": "מחזור מנהג אשכנז המערבי"})
    descriptions: dict = field(default_factory=dict)
    statements: list = field(default_factory=list)
    records: list = field(default_factory=list)
    work_candidate_evidence: list = field(default_factory=list)


class TestGateChecksTheSourcesAgree:
    def _report(self, item: _Item) -> dict[str, list[str]]:
        from app.pipeline.wikidata_export_quality_gate import (
            wikidata_export_quality_report,
        )

        return wikidata_export_quality_report([item])

    def test_the_exact_qdraft_work_37_shape_blocks_the_build(self) -> None:
        item = _Item(
            descriptions={
                "en": "Work preserved in a Hebrew manuscript, attested in NLI "
                      "record 990000592310205171",
            },
            records=["990000592310205171"],
            work_candidate_evidence=[
                {"title": "מחזור", "source_record_id": "990001253400205171"},
            ],
        )
        findings = self._report(item)["blocking"]
        assert any(f.startswith("WORK_EVIDENCE_RECORD_MISMATCH") for f in findings)

    def test_agreeing_sources_pass(self) -> None:
        item = _Item(
            descriptions={"en": "…, attested in NLI record 990000592310205171"},
            records=["990000592310205171"],
            work_candidate_evidence=[
                {"title": "x", "source_record_id": "990000592310205171"},
            ],
        )
        assert not any(
            f.startswith("WORK_EVIDENCE_RECORD_MISMATCH")
            for f in self._report(item)["blocking"]
        )

    def test_a_recordless_work_with_no_attestation_clause_passes(self) -> None:
        item = _Item(
            descriptions={"en": "Work preserved in a Hebrew manuscript"},
            work_candidate_evidence=[
                {"title": "x", "source_record_id": "", "source_scope": "authority"},
            ],
        )
        assert not any(
            f.startswith("WORK_EVIDENCE_RECORD_MISMATCH")
            for f in self._report(item)["blocking"]
        )

    def test_a_cited_record_the_item_does_not_own_blocks_the_build(self) -> None:
        item = _Item(
            descriptions={"en": "…, attested in NLI record 990009999999999999"},
            records=["990000592310205171"],
        )
        assert any(
            f.startswith("WORK_EVIDENCE_RECORD_MISMATCH")
            for f in self._report(item)["blocking"]
        )


class TestMergeDoesNotUnionAcrossRecords:
    @staticmethod
    def _work(local_id: str, *, records: list[str], evidence: list[dict] | None = None):
        from converter.wikidata.item_models import WikidataItem, WikidataStatement

        return WikidataItem(
            local_id=local_id,
            entity_type="work",
            labels={"he": "מחזור מנהג אשכנז המערבי"},
            statements=[
                WikidataStatement(property_id="P31", value="Q47461344", value_type="item"),
            ],
            records=records,
            work_candidate_evidence=evidence or [],
        )

    def test_a_label_match_across_disjoint_records_does_not_merge(self) -> None:
        from app.pipeline.wikidata_canonical_enrichment import merge_legacy_into_canonical

        canonical = self._work("QDraft_Work_37", records=["990000592310205171"])
        legacy = self._work("work:mahzor", records=["990001253400205171"])
        merged = merge_legacy_into_canonical([canonical], [legacy])
        # Two works attested from two records stay two works, and neither inherits
        # the other's record.
        by_id = {item.local_id: item for item in merged}
        assert by_id["QDraft_Work_37"].records == ["990000592310205171"]
        assert "990001253400205171" not in by_id["QDraft_Work_37"].records

    def test_a_label_match_with_a_shared_record_still_merges(self) -> None:
        from app.pipeline.wikidata_canonical_enrichment import merge_legacy_into_canonical

        canonical = self._work("QDraft_Work_37", records=["990000592310205171"])
        legacy = self._work(
            "work:mahzor",
            records=["990000592310205171"],
            evidence=[{"title": "x", "source_record_id": "990000592310205171"}],
        )
        merged = merge_legacy_into_canonical([canonical], [legacy])
        assert merged[0].work_candidate_evidence

    def test_the_union_drops_evidence_from_a_record_the_item_does_not_own(self) -> None:
        from app.pipeline.wikidata_canonical_enrichment import _union_work_evidence

        rows = _union_work_evidence(
            [{"title": "a", "source_record_id": "990000592310205171"}],
            [{"title": "b", "source_record_id": "990001253400205171"}],
            allowed_records={"990000592310205171"},
        )
        assert [r["title"] for r in rows] == ["a"]

    def test_the_union_keeps_two_records_attesting_the_same_title(self) -> None:
        """Deduping on the title alone silently kept only the first."""
        from app.pipeline.wikidata_canonical_enrichment import _union_work_evidence

        rows = _union_work_evidence(
            [{"title": "a", "source_record_id": "1", "source_field": "505"}],
            [{"title": "a", "source_record_id": "2", "source_field": "505"}],
        )
        assert len(rows) == 2
