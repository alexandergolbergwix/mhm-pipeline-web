"""Regression test for the Wikidata Studio creating zero work items.

**The bug** (Heroku app, 2026-06-02): the studio's items summary
reported ``works: 0`` even though the run had contents_ner WORK
entities in ``extraction_approvals``.

**The root cause**: ``app.pipeline.wikidata_studio.build_items_for_run``
attached ``marc_authority_matches`` to each record but never the
``entities`` list. The desktop ``WikidataItemBuilder`` creates work
items from ``record["entities"]`` filtered to
``source == "contents_ner"`` (item_builder.py:_add_works_and_authorities)
— without that channel populated, the work-creation path is dead.

**The fix**: ``build_items_for_run`` accepts ``entities_by_cn`` and
merges into ``record["entities"]``; the studio router loads them
from ``ExtractionApproval``.

This test pins the contract: pass contents_ner WORK entities through
``entities_by_cn`` → expect ``summary.works > 0``.
"""

from __future__ import annotations

import pytest

from app.pipeline import wikidata_studio


def _fake_marc_record(control_number: str = "1") -> dict:
    """Minimal MARC record shape the studio glue accepts."""
    return {
        "_control_number": control_number,
        "title":           "Some Hebrew Manuscript",
        "authors":         [],
        "contributors":    [],
        "subjects":        [],
        "dates":           {"year": 1500},
        "language":        "heb",
    }


def _fake_work_entity(text: str, control_number: str = "1") -> dict:
    """Shape matching what ``_load_entities_by_cn`` emits."""
    return {
        "text":             text,
        "type":             "WORK",
        "role":             "",
        "source":           "contents_ner",
        "start":            0,
        "end":              len(text),
        "confidence":       0.92,
        "model_confidence": 0.92,
        "approved":         True,
    }


class TestEntitiesByCnDrivesWorkCreation:
    """The contract Rule 47 + desktop's ``_add_works_and_authorities``
    rely on: NER WORK entities flowing through
    ``record["entities"]`` produce work items."""

    @pytest.mark.asyncio
    async def test_no_entities_zero_works(self) -> None:
        """Baseline — without entities the bug reproduces exactly."""
        result = await wikidata_studio.build_items_for_run(
            marc_records=[_fake_marc_record()],
            approved_matches=[],
            entities_by_cn=None,
            return_native=False,
        )
        assert result["summary"]["works"] == 0
        assert result["summary"]["manuscripts"] >= 1

    @pytest.mark.asyncio
    async def test_contents_ner_work_entities_create_work_items(self) -> None:
        """Fix invariant — contents_ner WORK entities on a record
        create work items."""
        ents = {
            "1": [
                _fake_work_entity("פירוש המשנה"),
                _fake_work_entity("ספר המצוות"),
            ],
        }
        result = await wikidata_studio.build_items_for_run(
            marc_records=[_fake_marc_record(control_number="1")],
            approved_matches=[],
            entities_by_cn=ents,
            return_native=False,
        )
        # Two distinct WORK titles → at least two work items. The
        # desktop builder dedupes within a record, so >= 2 is the
        # tight lower bound. Some builds may also emit a stub work
        # for a structured ``record["contents"]`` entry — we just
        # require the contents_ner path to fire.
        assert result["summary"]["works"] >= 2, (
            f"contents_ner WORK entities did not produce work items: "
            f"summary={result['summary']}"
        )

    @pytest.mark.asyncio
    async def test_empty_entities_by_cn_does_not_crash(self) -> None:
        """An empty dict is valid input (no entities for any record)."""
        result = await wikidata_studio.build_items_for_run(
            marc_records=[_fake_marc_record()],
            approved_matches=[],
            entities_by_cn={},
            return_native=False,
        )
        assert result["summary"]["manuscripts"] >= 1

    @pytest.mark.asyncio
    async def test_en_label_is_not_hebrew(self) -> None:
        """Regression for 2026-06-04 bug: Hebrew title must NOT appear in the
        ``en`` label slot. Both `en` and `he` were identical Hebrew strings."""
        import re

        hebrew_re = re.compile(r"[\u0590-\u05ff]")
        rec = _fake_marc_record(control_number="1")
        rec["title"] = "פנקס חשבונות של סוחר היושב במנטובה"
        rec["shelfmark"] = ""  # no shelfmark so the fallback logic is exercised

        result = await wikidata_studio.build_items_for_run(
            marc_records=[rec],
            approved_matches=[],
            entities_by_cn={},
            return_native=False,
        )
        for item in result["items"]:
            if item.get("entity_type") != "manuscript":
                continue
            en_label = (item.get("labels") or {}).get("en") or ""
            assert not (hebrew_re.search(en_label) and not re.search(r"[A-Za-z]", en_label)), (
                f"en label contains Hebrew-only text: {en_label!r}. "
                "Hebrew text must go in the `he` slot only."
            )

    @pytest.mark.asyncio
    async def test_entities_only_for_unmatched_cn_ignored(self) -> None:
        """An entity bucket for a control_number we never built must
        not crash the pipeline (silent skip)."""
        result = await wikidata_studio.build_items_for_run(
            marc_records=[_fake_marc_record(control_number="1")],
            approved_matches=[],
            entities_by_cn={"OTHER_CN": [_fake_work_entity("orphan")]},
            return_native=False,
        )
        assert result["summary"]["works"] == 0
