"""Regression tests for manuscript label fallbacks (WikiProject Manuscripts)."""

from __future__ import annotations

from converter.wikidata.item_builder import WikidataItemBuilder
from converter.wikidata.item_validator import validate_item


def test_manuscript_no_shelfmark_gets_cn_signature_label() -> None:
    builder = WikidataItemBuilder()
    record = {
        "_control_number": "990001801390205171",
        "title": "קובץ.",
    }
    item = builder.build_manuscript_item(record)
    assert item.labels.get("en") == "Jerusalem, NLI, 990001801390205171"
    assert item.labels.get("he") == "כתב יד עברי, ספרייה לאומית, 990001801390205171"


def test_manuscript_cn_fallback_passes_empty_label_validator() -> None:
    builder = WikidataItemBuilder()
    record = {
        "_control_number": "990001801390205171",
        "title": "קובץ.",
    }
    item = builder.build_manuscript_item(record)
    codes = {i.code for i in validate_item(item)}
    assert "EMPTY_LABEL" not in codes
