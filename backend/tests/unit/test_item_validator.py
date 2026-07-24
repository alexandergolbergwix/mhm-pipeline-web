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
    descriptions: dict = field(default_factory=dict)
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

    def test_q5_human_as_p31_on_manuscript_is_error(self) -> None:
        """Manuscripts must never have P31=Q5 (human)."""
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 5"},
            statements=[
                _Stmt("P31", "Q5"),  # ← human — always wrong for a manuscript
            ],
        )
        assert "P31_MANUSCRIPT_AS_HUMAN" in _codes(validate_item(item))

    def test_q5_human_as_p31_on_person_is_clean(self) -> None:
        """Person items legitimately use P31=Q5 — must NOT fire P31_WRONG_QID."""
        item = _Item(
            entity_type="person",
            labels={"en": "Moses Gaster"},
            statements=[
                _Stmt("P31", "Q5"),  # ← human — correct for a person
            ],
        )
        codes = _codes(validate_item(item))
        assert "P31_WRONG_QID" not in codes
        assert "P31_MANUSCRIPT_AS_HUMAN" not in codes

    def test_manuscript_qid_is_clean(self) -> None:
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 6"},
            statements=[
                _Stmt("P31", "Q87167"),   # manuscript — correct
            ],
        )
        assert "P31_WRONG_QID" not in _codes(validate_item(item))


# ── BAD_VALUE_QID ─────────────────────────────────────────────────────────────

class TestBadValueQid:
    """2026-06-04 audit: Q21857942 = Stolpersteine in Upper Austria was used
    as Q_POSSIBLY (P1480 value). Any statement or qualifier carrying this QID
    is corrupt data."""

    def test_stolpersteine_as_p1480_qualifier_is_error(self) -> None:
        """Q21857942 in a qualifier must be caught as BAD_VALUE_QID."""
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 7"},
            statements=[
                _Stmt(
                    "P1574", "Q12345",
                    qualifiers=[
                        {"property": "P1480", "value": "Q21857942", "type": "item"}
                    ],
                ),
            ],
        )
        issues = validate_item(item)
        assert "BAD_VALUE_QID" in _codes(issues)
        assert any(i.severity == "error" for i in issues if i.code == "BAD_VALUE_QID")

    def test_stolpersteine_as_statement_value_is_error(self) -> None:
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 8"},
            statements=[
                _Stmt("P1480", "Q21857942"),  # wrong directly as value
            ],
        )
        assert "BAD_VALUE_QID" in _codes(validate_item(item))

    def test_correct_possibly_qid_is_clean(self) -> None:
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 9"},
            statements=[
                _Stmt(
                    "P1574", "Q12345",
                    qualifiers=[
                        {"property": "P1480", "value": "Q30230067", "type": "item"}
                    ],
                ),
            ],
        )
        assert "BAD_VALUE_QID" not in _codes(validate_item(item))

    def test_presumably_qid_is_clean(self) -> None:
        """Q18122778 (presumably) is always valid."""
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 10"},
            statements=[
                _Stmt("P1480", "Q18122778"),
            ],
        )
        assert "BAD_VALUE_QID" not in _codes(validate_item(item))


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


# ── MARC artifact: "Collection" qualifier in person labels ─────────────────


class TestPersonNameQualifierStripping:
    """Regression for MARC-artifact qualifier words leaking into person labels.

    MARC sometimes encodes "Collection Gaster, Moses" (corporate name entry for
    a manuscript collection treated as a person entry). After _to_natural_name_order
    this becomes "Moses Collection Gaster".  The builder must strip the interior
    qualifier; the validator must not fire INSTITUTION_AS_PERSON.
    """

    def test_strip_collection_from_interior(self) -> None:
        from converter.wikidata.item_builder import _strip_person_name_qualifiers
        assert _strip_person_name_qualifiers("Moses Collection Gaster") == "Moses Gaster"
        assert _strip_person_name_qualifiers("David Papers Cohen") == "David Cohen"

    def test_boundary_collection_preserved(self) -> None:
        from converter.wikidata.item_builder import _strip_person_name_qualifiers
        assert _strip_person_name_qualifiers("Gaster Collection") == "Gaster Collection"
        assert _strip_person_name_qualifiers("Collection Gaster") == "Collection Gaster"

    def test_short_name_unchanged(self) -> None:
        from converter.wikidata.item_builder import _strip_person_name_qualifiers
        assert _strip_person_name_qualifiers("Moses Gaster") == "Moses Gaster"

    def test_validator_does_not_fire_for_interior_keyword(self) -> None:
        from converter.wikidata.item_validator import validate_item
        item = _Item(
            entity_type="person",
            labels={"en": "Moses Collection Gaster"},
            statements=[_Stmt("P31", "Q5")],
        )
        issues = validate_item(item)
        inst_issues = [i for i in issues if i.code == "INSTITUTION_AS_PERSON"]
        assert not inst_issues, (
            "INSTITUTION_AS_PERSON must not fire when the institutional keyword "
            f"is interior to a personal name: {[i.message for i in inst_issues]}"
        )

    def test_validator_fires_for_boundary_keyword(self) -> None:
        from converter.wikidata.item_validator import validate_item
        item = _Item(
            entity_type="person",
            labels={"en": "Gaster Collection"},
            statements=[_Stmt("P31", "Q5")],
        )
        issues = validate_item(item)
        codes = [i.code for i in issues]
        assert "INSTITUTION_AS_PERSON" in codes, (
            "INSTITUTION_AS_PERSON must fire when the institutional keyword is "
            "the last token of a 2-word label"
        )

    def test_pref_lat_inverted_with_trailing_collection(self) -> None:
        """Regression: pref_lat = 'Gaster, Moses Collection' from VIAF/Mazal
        must be un-inverted AND stripped before becoming the EN label so
        INVERTED_NAME_LABEL and INSTITUTION_AS_PERSON never fire.
        """
        from converter.wikidata.item_builder import (
            _normalise_label,
            _strip_person_name_qualifiers,
            _to_natural_name_order,
        )
        pref_lat = "Gaster, Moses Collection"
        cleaned = _normalise_label(
            _strip_person_name_qualifiers(_to_natural_name_order(pref_lat))
        )
        assert cleaned == "Moses Gaster", (
            f"pref_lat {pref_lat!r} should reduce to 'Moses Gaster', got {cleaned!r}"
        )

    def test_internal_hebrew_gershayim_is_not_quote_noise(self) -> None:
        item = _Item(
            entity_type="work",
            labels={"he": "ענף הג' פע\"ח והוא תיקוני עוונות"},
        )
        assert "LABEL_QUOTE_NOISE" not in _codes(validate_item(item))

    def test_terminal_hebrew_geresh_is_not_quote_noise(self) -> None:
        from converter.wikidata.item_validator import validate_item

        item = _Item(
            entity_type="manuscript",
            labels={"he": "נר ה'"},
            descriptions={"en": "Hebrew manuscript"},
        )
        assert not any(
            issue.code == "LABEL_QUOTE_NOISE" for issue in validate_item(item)
        )

    def test_validator_clean_after_pref_lat_fix(self) -> None:
        """After the pref_lat fix the built label is 'Moses Gaster' — the
        validator must produce zero INVERTED_NAME_LABEL / INSTITUTION_AS_PERSON
        errors for that item."""
        from converter.wikidata.item_validator import validate_item
        item = _Item(
            entity_type="person",
            labels={"en": "Moses Gaster"},
            statements=[_Stmt("P31", "Q5")],
        )
        issues = validate_item(item)
        bad_codes = {"INVERTED_NAME_LABEL", "INSTITUTION_AS_PERSON"}
        fired = [i.code for i in issues if i.code in bad_codes]
        assert not fired, (
            f"Unexpected errors for clean label 'Moses Gaster': {fired}"
        )

    def test_pref_heb_natural_order_for_he_label(self) -> None:
        from converter.wikidata.item_builder import (
            _normalise_label,
            _strip_person_name_qualifiers,
            _to_natural_name_order,
        )
        from converter.wikidata.item_validator import validate_item

        pref_heb = "דובנוב, שמעון"
        natural_he = _normalise_label(
            _strip_person_name_qualifiers(_to_natural_name_order(pref_heb))
        )
        assert natural_he == "שמעון דובנוב"
        item = _Item(
            entity_type="person",
            labels={"en": "Simon Dubnov", "he": natural_he},
            statements=[_Stmt("P31", "Q5"), _Stmt("P214", "123")],
        )
        codes = [i.code for i in validate_item(item)]
        assert "INVERTED_NAME_LABEL" not in codes


class TestP3959MarcControlNumber:
    """Regression tests for P3959 (NNL item ID / MARC 001) placement rules.

    P3959 is a BIBLIOGRAPHIC record identifier (Geagea complaint 2026-04-15):
      - MUST appear as a main statement on manuscript items.
      - MUST NOT appear as a main statement on person or work items.
    """

    def test_manuscript_without_p3959_is_error(self) -> None:
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Jerusalem, NLI, Ms. Heb. 4°1"},
            statements=[_Stmt("P31", "Q87167")],
        )
        issues = validate_item(item)
        assert "MISSING_P3959" in _codes(issues)
        assert any(i.severity == "error" for i in issues if i.code == "MISSING_P3959")

    def test_manuscript_with_p3959_is_clean(self) -> None:
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Jerusalem, NLI, Ms. Heb. 4°1"},
            statements=[
                _Stmt("P31", "Q87167"),
                _Stmt("P3959", "990000403370205171", "external-id"),
            ],
        )
        codes = [i.code for i in validate_item(item)]
        assert "MISSING_P3959" not in codes

    def test_discouraged_codex_p31_is_error(self) -> None:
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 1"},
            statements=[
                _Stmt("P31", "Q213924"),
                _Stmt("P31", "Q87167"),
                _Stmt("P3959", "990000403370205171", "external-id"),
            ],
        )
        issues = validate_item(item)
        assert "DISCOURAGED_P31" in _codes(issues)
        assert any(i.severity == "error" for i in issues if i.code == "DISCOURAGED_P31")

    def test_p17_p131_without_geo_evidence_is_error(self) -> None:
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 1"},
            statements=[
                _Stmt("P31", "Q87167"),
                _Stmt("P3959", "990000403370205171", "external-id"),
                _Stmt("P17", "Q801"),
                _Stmt("P131", "Q1218"),
            ],
        )
        assert "LOCATION_WITHOUT_GEO_EVIDENCE" in _codes(validate_item(item))

    def test_p50_somevalue_on_manuscript_is_error(self) -> None:
        item = _Item(
            entity_type="manuscript",
            labels={"en": "Hebrew manuscript, NLI, MS 1"},
            statements=[
                _Stmt("P31", "Q87167"),
                _Stmt("P3959", "990000403370205171", "external-id"),
                _Stmt("P50", None, "somevalue"),
            ],
        )
        assert "P50_ON_MANUSCRIPT" in _codes(validate_item(item))

    def test_p3959_on_person_item_is_error(self) -> None:
        item = _Item(
            entity_type="person",
            labels={"en": "Moses Gaster"},
            statements=[
                _Stmt("P31", "Q5"),
                _Stmt("P214", "51777166"),
                _Stmt("P3959", "990000403370205171", "external-id"),
            ],
        )
        codes = [i.code for i in validate_item(item)]
        assert "P3959_ON_NON_MANUSCRIPT" in codes, (
            "P3959 as a main statement on a person must raise P3959_ON_NON_MANUSCRIPT"
        )

    def test_p3959_on_work_item_is_error(self) -> None:
        item = _Item(
            entity_type="work",
            labels={"en": "Mishneh Torah"},
            statements=[
                _Stmt("P31", "Q7725634"),
                _Stmt("P3959", "990000403370205171", "external-id"),
            ],
        )
        codes = [i.code for i in validate_item(item)]
        assert "P3959_ON_NON_MANUSCRIPT" in codes

    def test_person_without_p3959_main_claim_is_clean(self) -> None:
        """Reference snaks may carry P3959; main claims must not."""
        item = _Item(
            entity_type="person",
            labels={"en": "Moses Gaster"},
            statements=[
                _Stmt("P31", "Q5"),
                _Stmt("P214", "51777166"),
            ],
        )
        codes = [i.code for i in validate_item(item)]
        assert "P3959_ON_NON_MANUSCRIPT" not in codes


class TestLabelHygieneWarnings:
    def test_generic_description_warning(self) -> None:
        item = _Item(
            labels={"en": "MS 1"},
            descriptions={"en": "item in the Hebrew Manuscripts Ontology (HMO)"},
        )
        assert "GENERIC_DESCRIPTION" in _codes(validate_item(item))

    def test_description_repeats_label_warning(self) -> None:
        item = _Item(
            labels={"en": "MS 1"},
            descriptions={"en": "MS 1"},
        )
        assert "DESCRIPTION_REPEATS_LABEL" in _codes(validate_item(item))

    def test_label_quote_noise_warning(self) -> None:
        item = _Item(labels={"en": '"Title" (in MS 1234)'})
        assert "LABEL_QUOTE_NOISE" in _codes(validate_item(item))

    def test_label_lang_mismatch_warning(self) -> None:
        item = _Item(labels={"he": "Jerusalem"})
        assert "LABEL_LANG_MISMATCH" in _codes(validate_item(item))

    def test_inverted_name_with_trailing_ibn_not_flagged(self) -> None:
        item = _Item(
            entity_type="person",
            labels={"he": "סיד, יצחק אבן"},
        )
        assert "INVERTED_NAME_LABEL" not in _codes(validate_item(item))
