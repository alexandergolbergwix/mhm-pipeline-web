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
    # A record designation, not an ownership claim (Rule W-161).
    assert item.labels.get("en") == "Hebrew manuscript, catalog record 990001801390205171"
    # Catalogue-record designation — never invent NLI as the holder (Rule W-161).
    assert item.labels.get("he") == "כתב יד עברי, 990001801390205171"


def test_manuscript_cn_fallback_passes_empty_label_validator() -> None:
    builder = WikidataItemBuilder()
    record = {
        "_control_number": "990001801390205171",
        "title": "קובץ.",
    }
    item = builder.build_manuscript_item(record)
    codes = {i.code for i in validate_item(item)}
    assert "EMPTY_LABEL" not in codes


def test_quoted_placeholder_title_uses_control_number_fallback() -> None:
    builder = WikidataItemBuilder()
    record = {
        "_control_number": "990001801390205171",
        "title": '"קובץ."',
    }
    item = builder.build_manuscript_item(record)
    assert item.labels["en"] == "Hebrew manuscript, catalog record 990001801390205171"
    assert item.labels["he"] == "כתב יד עברי, 990001801390205171"
    codes = {issue.code for issue in validate_item(item)}
    assert "KOVETZ_PLACEHOLDER" not in codes
