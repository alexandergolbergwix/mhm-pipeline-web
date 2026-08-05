"""Rule W-161 — a holder name is either audited or the build fails.

11 of 68 manuscripts in run 48ba6c13 shipped labelled "Jerusalem, NLI, F …"
while MARC 710 named Braginsky, Beit Ariela or "Unknown Library". Two things had
to be true at once for that: `_holding_institution_name` gated on a substring
keyword list that rejected "Braginsky Collection" (because "collection" lives in
the *person-name* qualifier words), and the label builder then filled the gap
with a hardcoded NLI default.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from converter.wikidata.holding_institutions import (
    STATUS_ABSTAINED,
    STATUS_PLACEHOLDER,
    STATUS_RESOLVED,
    STATUS_UNKNOWN,
    institution_label,
    resolve_first_holder,
    resolve_holder,
    unknown_holder_names,
)
from converter.wikidata.item_builder import (
    _holding_institution_name,
    holder_names_from_record,
    manuscript_en_label,
    manuscript_he_designation,
    manuscript_record_label,
)


def _record(*owners: str, holding: str = "") -> dict[str, object]:
    return {
        "contributors": [
            {"name": name, "role": '"current owner', "field": "710"}
            for name in owners
        ],
        "holding_institution": holding,
        "languages": ["heb"],
    }


class TestResolveHolder:
    def test_a_verified_name_resolves_to_its_qid_and_label(self) -> None:
        r = resolve_holder('"The British Library')
        assert (r.status, r.qid, r.label) == (STATUS_RESOLVED, "Q23308", "British Library")

    @pytest.mark.parametrize(
        ("name", "qid"),
        [
            ("Braginsky Collection of Hebrew Manuscripts and Printed Books", "Q4955432"),
            ("Yeshiva University Library", "Q115654253"),
            ("Central Archives for the History of the Jewish People", "Q2893584"),
        ],
    )
    def test_the_three_names_that_became_nli_now_resolve(self, name, qid) -> None:
        assert resolve_holder(name).qid == qid

    def test_an_unaudited_name_is_unknown_not_a_silent_none(self) -> None:
        r = resolve_holder("Nonesuch Institute Library")
        assert r.status == STATUS_UNKNOWN
        assert "audited" in r.reason
        assert r.attested is True

    def test_a_reviewed_abstention_is_distinguishable_from_an_unaudited_miss(self) -> None:
        r = resolve_holder("The Montefiore Library")
        assert r.status == STATUS_ABSTAINED
        assert r.reason
        # Still attested: the record does name a holder, so the label must say so.
        assert r.attested is True
        assert r.display_name == "The Montefiore Library"

    def test_a_placeholder_attests_no_holder_at_all(self) -> None:
        r = resolve_holder("Unknown Library")
        assert r.status == STATUS_PLACEHOLDER
        assert r.attested is False
        assert r.display_name == ""

    def test_a_resolved_name_prefers_the_verified_label_over_the_marc_spelling(self) -> None:
        assert resolve_holder("The Russian State Library").display_name == (
            "Russian State Library"
        )

    def test_resolve_first_holder_prefers_a_resolved_name(self) -> None:
        r = resolve_first_holder(["Private Collection", "The British Library"])
        assert r.qid == "Q23308"

    def test_resolve_first_holder_returns_none_when_nothing_is_attested(self) -> None:
        assert resolve_first_holder(["Unknown Library", ""]) is None

    def test_unknown_holder_names_lists_only_the_unaudited(self) -> None:
        assert unknown_holder_names([
            "The British Library", "Unknown Library",
            "The Montefiore Library", "Nonesuch Library",
        ]) == ["Nonesuch Library"]


class TestHolderNameExtraction:
    def test_braginsky_is_no_longer_rejected_as_a_non_institution(self) -> None:
        """"collection" is a person-name qualifier word, not an institution word."""
        record = _record("Braginsky Collection of Hebrew Manuscripts and Printed Books")
        assert _holding_institution_name(record) == "Braginsky Collection"

    def test_a_placeholder_holder_yields_no_name(self) -> None:
        assert _holding_institution_name(_record("Unknown Library")) == ""

    def test_an_abstained_holder_still_yields_its_attested_name(self) -> None:
        assert _holding_institution_name(_record("Private Collection")) == (
            "Private Collection"
        )

    def test_the_label_and_p195_read_the_same_names(self) -> None:
        from converter.wikidata.manuscript_projection import _current_holder_names

        record = _record("The British Library", holding="Cambridge University Library")
        assert _current_holder_names(record) == holder_names_from_record(record)


class TestManuscriptLabels:
    def test_a_resolved_holder_uses_the_audited_label(self) -> None:
        assert manuscript_en_label("F 8298", "British Library") == (
            "British Library, F 8298"
        )

    def test_an_unlinkable_holder_is_still_named(self) -> None:
        """Attestation, not fabrication — the record's own 710 string."""
        assert manuscript_en_label("F 41164", "Braginsky Collection") == (
            "Braginsky Collection, F 41164"
        )

    def test_no_holder_means_the_shelfmark_alone_never_nli(self) -> None:
        label = manuscript_en_label("F 22325", "")
        assert label == "F 22325"
        assert "NLI" not in label

    def test_the_no_shelfmark_fallback_is_a_record_designation(self) -> None:
        label = manuscript_record_label("990001801390205171")
        assert label == "Hebrew manuscript, NLI record 990001801390205171"
        assert "Jerusalem" not in label

    def test_the_hebrew_designation_names_the_real_holder(self) -> None:
        label = manuscript_he_designation(
            {"languages": ["ita"]}, "F 9", holder_name="Russian State Library",
        )
        assert "Russian State Library" in label
        assert "הספרייה הלאומית" not in label

    def test_the_hebrew_designation_omits_an_unattested_holder(self) -> None:
        assert manuscript_he_designation({"languages": ["heb"]}, "F 1") == (
            "כתב יד עברי, F 1"
        )


@dataclass
class _Stmt:
    property_id: str
    value: str = ""


@dataclass
class _Item:
    local_id: str = "QDraft_MS_1"
    entity_type: str = "manuscript"
    labels: dict = field(default_factory=dict)
    statements: list = field(default_factory=list)
    records: list = field(default_factory=list)


class TestGateHolderFindings:
    _CN = "990001882630205171"

    def _marc(self, owner: str) -> list[dict[str, object]]:
        return [{
            "_control_number": self._CN,
            "control_number": self._CN,
            "contributors": [{"name": owner, "role": '"current owner', "field": "710"}],
        }]

    def _report(self, item: _Item, owner: str) -> dict[str, list[str]]:
        from app.pipeline.wikidata_export_quality_gate import (
            wikidata_export_quality_report,
        )

        return wikidata_export_quality_report(
            [item], serialised_items=[], marc_records=self._marc(owner),
        )

    def _item(self, label: str, *statements: _Stmt) -> _Item:
        return _Item(
            labels={"en": label},
            statements=[_Stmt("P31", "Q87167"), _Stmt("P3959", self._CN), *statements],
            records=[self._CN],
        )

    def test_an_unaudited_holder_blocks_the_build(self) -> None:
        report = self._report(
            self._item("Nonesuch Library, F 41164"), "Nonesuch Library",
        )
        assert any(f.startswith("UNAUDITED_HOLDER") for f in report["blocking"])

    def test_a_reviewed_abstention_is_informational_not_blocking(self) -> None:
        report = self._report(
            self._item("The Montefiore Library, F 5359"), "The Montefiore Library",
        )
        assert not any("HOLDER" in f for f in report["blocking"])
        assert any(f.startswith("HOLDER_ABSTAINED") for f in report["informational"])

    def test_an_nli_label_over_another_holder_blocks_the_build(self) -> None:
        """The exact run-48ba6c13 regression."""
        report = self._report(
            self._item("Jerusalem, NLI, F 41164"), "Braginsky Collection",
        )
        assert any(f.startswith("FABRICATED_HOLDER") for f in report["blocking"])

    def test_an_nli_label_is_fine_when_nli_really_holds_it(self) -> None:
        report = self._report(
            self._item("Jerusalem, NLI, F 41164"), "The National Library of Israel",
        )
        assert not any(f.startswith("FABRICATED_HOLDER") for f in report["blocking"])

    def test_a_p195_that_is_not_the_audited_qid_blocks_the_build(self) -> None:
        report = self._report(
            self._item("Braginsky Collection, F 41164", _Stmt("P195", "Q188915")),
            "Braginsky Collection",
        )
        assert any(f.startswith("HOLDER_QID_UNVERIFIED") for f in report["blocking"])

    def test_the_audited_p195_passes(self) -> None:
        report = self._report(
            self._item("Braginsky Collection, F 41164", _Stmt("P195", "Q4955432")),
            "Braginsky Collection",
        )
        assert not any("HOLDER" in f for f in report["blocking"])

    def test_a_record_naming_no_holder_is_informational(self) -> None:
        report = self._report(self._item("F 22325"), "Unknown Library")
        assert not any("HOLDER" in f for f in report["blocking"])
        assert any(f.startswith("HOLDER_UNATTESTED") for f in report["informational"])
