"""Export-33 / Rule W-172 regressions — synthetic, no CN allowlists."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_EVAL_AGENT_ROOT = Path(__file__).resolve().parents[3] / "eval-agent"
if str(_EVAL_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_AGENT_ROOT))

from app.pipeline.hmo_canonical_wikidata import (  # noqa: E402
    _filter_person_aliases,
    _hebrew_manuscript_description,
)
from app.pipeline.wikidata_verdict_cache import WIKIDATA_VERDICT_SCHEMA  # noqa: E402
from converter.wikidata.catalog_notes import is_catalog_note_placeholder  # noqa: E402
from converter.wikidata.item_builder import (  # noqa: E402
    _build_work_description_for_record,
)
from eval_agent.ingest.wikidata_items import compact_statements  # noqa: E402


class TestJudgeFixtureKeepsQuantityUnit:
    def test_compact_statements_preserves_leaf_unit(self) -> None:
        rows = compact_statements({
            "statements": [{
                "property": "P1104",
                "value": 245,
                "unit": "Q107256474",
                "value_type": "quantity",
            }],
        })
        assert rows[0]["unit"] == "Q107256474"
        assert rows[0]["value_type"] == "quantity"

    def test_schema_bumped_for_unit_fixture(self) -> None:
        assert WIKIDATA_VERDICT_SCHEMA == "w174_v1"


class TestHebrewDescriptionNotLabelClone:
    def test_hebrew_description_omits_shelfmark(self) -> None:
        text = _hebrew_manuscript_description({
            "languages": ["heb"],
            "holding_institution": "Jewish Theological Seminary Library",
            "shelfmark": "F 29346",
            "dates": {"original_string": "16th century"},
        })
        assert "F 29346" not in text
        assert text.startswith("כתב יד עברי")


class TestCatalogNoteNotInscription:
    def test_scholarly_attribution_is_catalog_note(self) -> None:
        assert is_catalog_note_placeholder(
            "מיוסד על ס' ערמת חטים מאת יהודה דה פורטה. לפי דעת קאסוטו המחבר הוא שלמה."
        )


class TestFacsimileWorkDescription:
    def test_facsimile_source_not_called_manuscript_work(self) -> None:
        text = _build_work_description_for_record(
            author_name="ועד הקהילות",
            century=None,
            source_record={"notes": ['500$a: "דפוס צלום של הוצאת ברלין"']},
        )
        assert "preserved in a Hebrew manuscript" not in text.lower()
        assert "facsimile" in text.lower()


class TestPersonAliasIdentityFilter:
    def test_drops_other_person_aliases(self) -> None:
        out = _filter_person_aliases(
            {
                "he": [
                    "אלפקעה, שלמה בן סעדיה",
                    "סעדיה בן שלמה טויל",
                ],
            },
            {"he": "סעדיה בן שלמה"},
        )
        assert out == {}

    def test_keeps_inverted_form_of_same_person(self) -> None:
        out = _filter_person_aliases(
            {"he": ["אלפקעה, שלמה בן סעדיה"]},
            {"he": "שלמה בן סעדיה אלפקעה"},
        )
        assert out["he"] == ["אלפקעה, שלמה בן סעדיה"]
