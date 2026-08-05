"""Rule W-142 — projection fixes found by auditing export (18)."""

from __future__ import annotations

from app.pipeline.hmo_canonical_wikidata import (
    _hebrew_manuscript_description,
    _hebrew_manuscript_label,
)
from app.pipeline.wikidata_canonical_enrichment import merge_legacy_into_canonical
from app.pipeline.wikidata_local_refs import resolve_local_references
from converter.wikidata.item_models import WikidataItem, WikidataStatement


def _stmt(pid: str, value: str, *, value_type: str = "item", **kw) -> WikidataStatement:
    return WikidataStatement(property_id=pid, value=value, value_type=value_type, **kw)


def _contained(target: str, stated: str) -> WikidataStatement:
    return _stmt(
        "P1574", f"__LOCAL:{target}",
        qualifiers=[{"property": "P1932", "value": stated,
                     "value_type": "monolingualtext"}],
    )


class TestContainedWorkRelinking:
    """32 of 42 degraded P1574 claims named a work that WAS in the build."""

    def test_relinks_by_work_label(self) -> None:
        work = WikidataItem(
            entity_type="work", local_id="QDraft_Work_71",
            labels={"he": "פרי עץ חיים ענף ראשון"}, statements=[],
        )
        ms = WikidataItem(
            entity_type="manuscript", local_id="QDraft_MS_1",
            statements=[_contained("QDraft_Work_stale", "פרי עץ חיים ענף ראשון")],
        )
        stats = resolve_local_references([work, ms])
        assert stats["relinked"] == 1
        assert stats["degraded"] == 0
        assert ms.statements[0].value == "__LOCAL:QDraft_Work_71"

    def test_relinks_by_work_title_claim(self) -> None:
        work = WikidataItem(
            entity_type="work", local_id="QDraft_Work_9", labels={},
            statements=[_stmt("P1476", "נר ה'", value_type="monolingualtext")],
        )
        ms = WikidataItem(
            entity_type="manuscript", local_id="QDraft_MS_2",
            statements=[_contained("QDraft_Work_gone", "נר ה'")],
        )
        assert resolve_local_references([work, ms])["relinked"] == 1
        assert ms.statements[0].value == "__LOCAL:QDraft_Work_9"

    def test_a_genuine_orphan_still_degrades(self) -> None:
        ms = WikidataItem(
            entity_type="manuscript", local_id="QDraft_MS_3",
            statements=[_contained("QDraft_Work_absent", "עבודה שלא נבנתה")],
        )
        stats = resolve_local_references([ms])
        assert stats == {"degraded": 1, "dropped": 0, "relinked": 0}
        assert ms.statements[0].value == "Q234460"

    def test_never_creates_a_self_reference(self) -> None:
        """The circular P1574 the judge flagged must stay impossible."""
        work = WikidataItem(
            entity_type="work", local_id="QDraft_Work_self",
            labels={"he": "מגלת אסתר"},
            statements=[_contained("gone", "מגלת אסתר")],
        )
        assert resolve_local_references([work])["relinked"] == 0
        assert work.statements[0].value == "Q234460"


class TestCanonicalMergeKeepsDistinctUrls:
    def test_catalogue_url_survives_beside_the_hmo_bridge(self) -> None:
        """P973 holds both; suppressing by PID dropped the catalogue link."""
        canonical = WikidataItem(
            entity_type="manuscript", local_id="QDraft_MS_1", records=["990001"],
            statements=[
                _stmt("P31", "Q87167"),
                _stmt("P973", "https://mhm-hmo.wikibase.cloud/wiki/Item:Q893",
                      value_type="url"),
            ],
        )
        legacy = WikidataItem(
            entity_type="manuscript", local_id="QDraft_MS_1", records=["990001"],
            statements=[
                _stmt("P31", "Q213924"),
                _stmt("P973", "http://www.bl.uk/manuscripts/x", value_type="url"),
            ],
        )
        merged = merge_legacy_into_canonical([canonical], [legacy])[0]
        urls = {str(s.value) for s in merged.statements if s.property_id == "P973"}
        assert urls == {
            "https://mhm-hmo.wikibase.cloud/wiki/Item:Q893",
            "http://www.bl.uk/manuscripts/x",
        }

    def test_p31_stays_canonical_only(self) -> None:
        """Merging legacy P31 back would reintroduce discouraged types (W-98)."""
        canonical = WikidataItem(
            entity_type="manuscript", local_id="QDraft_MS_2", records=["990002"],
            statements=[_stmt("P31", "Q87167")],
        )
        legacy = WikidataItem(
            entity_type="manuscript", local_id="QDraft_MS_2", records=["990002"],
            statements=[_stmt("P31", "Q213924")],
        )
        merged = merge_legacy_into_canonical([canonical], [legacy])[0]
        assert [str(s.value) for s in merged.statements if s.property_id == "P31"] == [
            "Q87167",
        ]

    def test_printed_facsimile_is_not_typed_as_a_manuscript(self) -> None:
        canonical = WikidataItem(
            entity_type="manuscript", local_id="QDraft_MS_3", records=["990003"],
            statements=[_stmt("P31", "Q87167")],
        )
        legacy = WikidataItem(
            entity_type="manuscript", local_id="QDraft_MS_3", records=["990003"],
            semantic_type="printed_facsimile",
            statements=[_stmt("P31", "Q571")],
        )
        merged = merge_legacy_into_canonical([canonical], [legacy])[0]
        types = {str(s.value) for s in merged.statements if s.property_id == "P31"}
        assert types == {"Q571"}
        assert merged.semantic_type == "printed_facsimile"


class TestHebrewLabelAndDescription:
    def test_label_names_the_real_holder_not_nli(self) -> None:
        """An Israel Museum manuscript announced the National Library."""
        label = _hebrew_manuscript_label(
            {"languages": ["heb"], "holding_institution": "The Israel Museum"},
            "F 32638",
        )
        assert "מוזיאון ישראל" in label
        assert "ספרייה לאומית" not in label

    def test_label_language_follows_the_record(self) -> None:
        label = _hebrew_manuscript_label({"languages": ["ara"]}, "Ms. Heb. 1")
        assert label.startswith("כתב יד ערבי")

    def test_foreign_holder_keeps_its_own_name(self) -> None:
        label = _hebrew_manuscript_label(
            {"languages": ["ita"], "holding_institution": "The Russian State Library"},
            "F 9",
        )
        # The verified table label ("Russian State Library") wins over the MARC
        # spelling — one audited name, not whatever the cataloguer typed
        # (Rule W-161). The point of the test is that NLI is not substituted.
        assert "Russian State Library" in label
        assert "הספרייה הלאומית" not in label

    def test_printed_facsimile_description_is_not_called_a_manuscript(self) -> None:
        text = _hebrew_manuscript_description({
            "languages": ["heb"],
            "dates": {"year": "1969"},
            "notes": ['500$a: "דפוס צלום"'],
        })
        assert "כתב יד" not in text
        assert "פקסימיליה" in text
