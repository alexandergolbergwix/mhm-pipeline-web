from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.analyze_wikidata_verdicts import _rows


def test_rows_keep_only_non_passing_verdicts_and_prompt_context(tmp_path: Path) -> None:
    source = tmp_path / "verdicts.csv"
    with source.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "local_id",
                "entity_type",
                "label_en",
                "ai_verdict_overall",
                "statements_json",
                "marc_context_json",
                "ai_verdict_json",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "local_id": "pass",
                "entity_type": "work",
                "label_en": "Pass",
                "ai_verdict_overall": "pass",
                "statements_json": "[]",
                "marc_context_json": "{}",
                "ai_verdict_json": "{}",
            }
        )
        writer.writerow(
            {
                "local_id": "bad",
                "entity_type": "work",
                "label_en": "Bad",
                "ai_verdict_overall": "partial",
                "statements_json": '[{"property_id":"P31"}]',
                "marc_context_json": '{"title":"MARC title"}',
                "ai_verdict_json": '{"overall":"partial","reasoning":"Needs evidence"}',
            }
        )

    rows = _rows(source)

    assert len(rows) == 1
    assert rows[0]["local_id"] == "bad"
    assert rows[0]["verdict"]["overall"] == "partial"
    assert rows[0]["verdict"]["reasoning"] == "Needs evidence"
    assert rows[0]["marc_context"]["title"] == "MARC title"
    assert rows[0]["statements"][0]["property_id"] == "P31"


def test_rows_accept_json_items_export(tmp_path: Path) -> None:
    source = tmp_path / "items.json"
    source.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "local_id": "json-bad",
                        "entity_type": "person",
                        "labels": {"en": "Person"},
                        "ai_verdict": {"overall": "fail", "reasoning": "Wrong type"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    rows = _rows(source)

    assert len(rows) == 1
    assert rows[0]["local_id"] == "json-bad"
    assert rows[0]["verdict"]["overall"] == "fail"
