"""Tests for merging AI Wikidata autofixes into override payloads."""

from app.pipeline.wikidata_autofix_apply import merge_ai_fixes


def test_merge_label_fixes():
    out = merge_ai_fixes([
        {"target": "label.he", "value": "שמעון דובנוב", "confidence": "high"},
        {"target": "label.en", "value": "Simon Dubnow", "confidence": "high"},
    ])
    assert out["labels"]["he"] == "שמעון דובנוב"
    assert out["labels"]["en"] == "Simon Dubnow"


def test_skips_non_high_confidence():
    out = merge_ai_fixes([
        {"target": "label.he", "value": "ignored", "confidence": "medium"},
    ])
    assert "labels" not in out


def test_merge_statement_remove_and_add():
    out = merge_ai_fixes(
        [
            {"target": "statement.remove", "studio_statement_index": 2, "confidence": "high"},
            {
                "target": "statement.add",
                "property_id": "P214",
                "value": "12345",
                "value_type": "external-id",
                "confidence": "high",
            },
        ],
        remove_statements=[1],
        add_statements=[{"property_id": "P31", "value": "Q5"}],
    )
    assert out["remove_statements"] == [1, 2]
    assert len(out["add_statements"]) == 2
    assert out["add_statements"][-1]["property_id"] == "P214"
