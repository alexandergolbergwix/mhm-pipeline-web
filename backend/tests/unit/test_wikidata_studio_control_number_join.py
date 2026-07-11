"""Regression coverage for quoted control-number joins in Studio builds."""

from __future__ import annotations

import pytest

from app.pipeline.wikidata_studio import build_items_for_run


@pytest.mark.asyncio
async def test_quoted_authority_and_entity_keys_join_clean_marc_control_number() -> None:
    control_number = "990000000000000001"
    result = await build_items_for_run(
        marc_records=[{
            "_control_number": f'"{control_number}"',
            "title": "כתב יד לדוגמה",
            "authors": [],
            "contributors": [],
            "subjects": [],
        }],
        approved_matches=[{
            "control_number": f'"{control_number}"',
            "entity_text": "אברהם בדיקה",
            "role": "author",
            "field": "100",
            "mazal_id": "987000000000000001",
            "viaf_id": "",
            "wikidata_qid": "",
            "confidence": "high",
            "source": "mazal",
            "payload": {},
        }],
        entities_by_cn={
            f'"{control_number}"': [{
                "text": "חיבור בדיקה",
                "type": "WORK",
                "role": "",
                "source": "contents_ner",
                "start": 0,
                "end": 10,
                "confidence": 0.99,
                "model_confidence": 0.99,
            }],
        },
    )

    assert result["summary"]["manuscripts"] == 1
    assert result["summary"]["persons"] == 1
    assert result["summary"]["works"] == 1
    person = next(item for item in result["items"] if item["entity_type"] == "person")
    assert person["records"] == [control_number]
    assert any(statement["property_id"] == "P8189" for statement in person["statements"])
