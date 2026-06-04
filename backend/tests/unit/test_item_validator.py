"""Unit tests for converter.wikidata.item_validator.

Every test here is a regression guard for a real Wikidata community complaint
or a property-audit finding. The test name encodes the incident date and the
validator code being pinned.

2026-06-04 property audit:
  - P50_ON_MANUSCRIPT: explicit Property:P50 constraint violation
  - P7416_AS_QUANTITY: wrong use of citation-folio qualifier as count property
  - P31_WRONG_QID: copy-paste error (Q179808 = Palme d'Or not palimpsest)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from converter.wikidata.item_validator import validate_item, ValidationIssue


# ── Minimal stub types ────────────────────────────────────────────────────────

@dataclass
class _Stmt:
    property_id: str
    value: Any = ""
    value_type: str = "item"
    language: str = ""
    qualifiers: list = field(default_factory=list)
    references: list = field(default_factory=list)


@dataclass
class _Item:
    entity_type: str = "manuscript"
    labels: dict = field(default_factory=lambda: {"en": "Hebrew manuscript, NLI, MS 1234"})
    statements: list = field(default_factory=list)
    existing_qid: str = ""


def _codes(issues: list[ValidationIssue]) -> set[str]:
    return {i.code for i in issues}


# ── P50_ON_MANUSCRIPT ─────────────────────────────────────────────────────────

class TestP50OnManuscript:
    """2026-06-04 audit: Property:P50 constraint forbids P50 on manuscripts."""

    def test_p50_on_manuscript_is_error(self) -> None:
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 1"},
            statements=[
                _Stmt("P31", "Q87167"),
                _Stmt("P50", "Q12345"),   # ← direct P50 on MS — forbidden
            ],
        )
        issues = validate_item(item)
        assert "P50_ON_MANUSCRIPT" in _codes(issues), (
            "Validator must flag P50 directly on a manuscript item"
        )
        assert any(i.severity == "error" for i in issues if i.code == "P50_ON_MANUSCRIPT")

    def test_p50_on_work_is_clean(self) -> None:
        item = _Item(
            entity_type="work",
            labels={"en": "Mishneh Torah"},
            statements=[
                _Stmt("P31", "Q47461344"),
                _Stmt("P50", "Q12345"),   # ← P50 on a work is correct
            ],
        )
        issues = validate_item(item)
        assert "P50_ON_MANUSCRIPT" not in _codes(issues)

    def test_p11603_on_manuscript_is_allowed(self) -> None:
        """P11603 (transcribed by / scribe) IS a direct-manuscript property."""
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 1"},
            statements=[
                _Stmt("P31", "Q87167"),
                _Stmt("P11603", "Q99999"),   # scribe → OK
            ],
        )
        issues = validate_item(item)
        assert "P50_ON_MANUSCRIPT" not in _codes(issues)

    def test_p50_local_placeholder_on_manuscript_is_error(self) -> None:
        """__LOCAL: references also must not carry P50 on manuscripts."""
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 1"},
            statements=[
                _Stmt("P50", "__LOCAL:work_foo", value_type="item"),
            ],
        )
        assert "P50_ON_MANUSCRIPT" in _codes(validate_item(item))


# ── P7416_AS_QUANTITY ─────────────────────────────────────────────────────────

class TestP7416AsQuantity:
    """2026-06-04 audit: P7416 is a STRING citation-folio qualifier,
    not a count/quantity property."""

    def test_p7416_with_quantity_type_is_error(self) -> None:
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 2"},
            statements=[
                _Stmt("P31", "Q87167"),
                _Stmt("P7416", 120, value_type="quantity"),  # ← wrong
            ],
        )
        issues = validate_item(item)
        assert "P7416_AS_QUANTITY" in _codes(issues)
        assert any(i.severity == "error" for i in issues if i.code == "P7416_AS_QUANTITY")

    def test_p7416_as_string_is_clean(self) -> None:
        """P7416 used as a folio-reference string qualifier is correct."""
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 2"},
            statements=[
                _Stmt("P31", "Q87167"),
                # P7416 as string (e.g. "15r") is the intended use
                _Stmt("P7416", "15r", value_type="string"),
            ],
        )
        issues = validate_item(item)
        assert "P7416_AS_QUANTITY" not in _codes(issues)

    def test_p1104_with_leaf_unit_is_clean(self) -> None:
        """The correct way to record folio count: P1104 + unit Q107256474."""
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 3"},
            statements=[
                _Stmt("P31", "Q87167"),
                _Stmt("P1104", 120, value_type="quantity"),  # ← correct
            ],
        )
        issues = validate_item(item)
        assert "P7416_AS_QUANTITY" not in _codes(issues)


# ── P31_WRONG_QID ─────────────────────────────────────────────────────────────

class TestP31WrongQid:
    """2026-06-04 audit: Q_PALIMPSEST was Q179808 (Palme d'Or — Cannes award)
    instead of Q274076 (palimpsest). Every item with a palimpsest flag would
    have been tagged 'instance of: Palme d'Or'."""

    def test_palme_dor_as_p31_is_error(self) -> None:
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 4"},
            statements=[
                _Stmt("P31", "Q179808"),  # ← Palme d'Or! wrong
            ],
        )
        issues = validate_item(item)
        assert "P31_WRONG_QID" in _codes(issues)
        assert any(i.severity == "error" for i in issues if i.code == "P31_WRONG_QID")

    def test_correct_palimpsest_qid_is_clean(self) -> None:
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 4"},
            statements=[
                _Stmt("P31", "Q87167"),
                _Stmt("P31", "Q274076"),  # ← real palimpsest QID
            ],
        )
        issues = validate_item(item)
        assert "P31_WRONG_QID" not in _codes(issues)

    def test_q5_human_as_p31_is_error(self) -> None:
        """Manuscripts must never have P31=Q5 (human)."""
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 5"},
            statements=[
                _Stmt("P31", "Q5"),  # ← human — always wrong for a manuscript
            ],
        )
        assert "P31_WRONG_QID" in _codes(validate_item(item))

    def test_manuscript_qid_is_clean(self) -> None:
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 6"},
            statements=[
                _Stmt("P31", "Q87167"),   # manuscript — correct
            ],
        )
        assert "P31_WRONG_QID" not in _codes(validate_item(item))


# ── Integration: builder never produces these violations ─────────────────────

class TestBuilderNeverViolatesNewChecks:
    """End-to-end: build_items_for_run output must pass all new validator rules."""

    @pytest.mark.asyncio
    async def test_manuscript_item_has_no_p50(self) -> None:
        """After the 2026-06-04 fix, built manuscript items must have zero P50."""
        from app.pipeline import wikidata_studio

        rec = {
            "_control_number": "1",
            "title": "פירוש על התורה",
            "authors": ["Moses Maimonides"],
            "contributors": [],
            "subjects": [],
            "dates": {"year": 1300},
            "language": "heb",
            "marc_authority_matches": [
                {
                    "name": "Maimonides",
                    "role": "author",
                    "viaf_uri": "http://viaf.org/viaf/100185495",
                    "mazal_id": None,
                }
            ],
        }
        result = await wikidata_studio.build_items_for_run(
            marc_records=[rec],
            approved_matches=[],
            entities_by_cn={},
            return_native=True,
        )
        from converter.wikidata.item_validator import validate_item
        for item in result["native_items"]:
            if item.entity_type != "manuscript":
                continue
            p50_values = [
                s for s in item.statements if s.property_id == "P50"
            ]
            assert not p50_values, (
                f"Manuscript item still has P50 directly: "
                f"{[s.value for s in p50_values]}"
            )
            issues = validate_item(item)
            p50_issues = [i for i in issues if i.code == "P50_ON_MANUSCRIPT"]
            assert not p50_issues, (
                f"Validator found P50_ON_MANUSCRIPT on built item: {p50_issues}"
            )

    @pytest.mark.asyncio
    async def test_palimpsest_flag_uses_correct_qid(self) -> None:
        """Manuscripts with palimpsest=True must have P31=Q274076, not Q179808."""
        from app.pipeline import wikidata_studio

        rec = {
            "_control_number": "99",
            "title": "פלימפססט עברי",
            "authors": [],
            "contributors": [],
            "subjects": [],
            "dates": {"year": 1200},
            "language": "heb",
            "is_palimpsest": True,
        }
        result = await wikidata_studio.build_items_for_run(
            marc_records=[rec],
            approved_matches=[],
            entities_by_cn={},
            return_native=True,
        )
        from converter.wikidata.item_validator import validate_item
        for item in result["native_items"]:
            if item.entity_type != "manuscript":
                continue
            issues = validate_item(item)
            bad = [i for i in issues if i.code == "P31_WRONG_QID"]
            assert not bad, (
                f"Manuscript has wrong P31 QID: {[i.message for i in bad]}"
            )
            p31_values = [
                s.value for s in item.statements if s.property_id == "P31"
            ]
            assert "Q179808" not in p31_values, (
                "Palme d'Or (Q179808) must never appear as P31 on a manuscript"
            )

    @pytest.mark.asyncio
    async def test_folio_count_uses_p1104_not_p7416(self) -> None:
        """Physical folio count must use P1104 (quantity), never P7416 (quantity)."""
        from app.pipeline import wikidata_studio
        from converter.wikidata.item_validator import validate_item

        rec = {
            "_control_number": "42",
            "title": "כתב יד עם כמה דפים",
            "authors": [],
            "contributors": [],
            "subjects": [],
            "dates": {"year": 1400},
            "language": "heb",
            "extent": "120 leaves",
        }
        result = await wikidata_studio.build_items_for_run(
            marc_records=[rec],
            approved_matches=[],
            entities_by_cn={},
            return_native=True,
        )
        for item in result["native_items"]:
            if item.entity_type != "manuscript":
                continue
            issues = validate_item(item)
            qty_issues = [i for i in issues if i.code == "P7416_AS_QUANTITY"]
            assert not qty_issues, (
                f"Builder emitted P7416 as quantity on manuscript: "
                f"{[i.message for i in qty_issues]}"
            )
            p7416_stmts = [
                s for s in item.statements
                if s.property_id == "P7416" and s.value_type == "quantity"
            ]
            assert not p7416_stmts, (
                "P7416 must not be used as a quantity; use P1104 with unit leaf"
            )
