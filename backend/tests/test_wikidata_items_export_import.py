"""Export/import round-trip for Wikidata Studio item overrides."""

from __future__ import annotations

import csv
import json

import pytest

from app.models.item_override import WikidataItemOverride
from app.models.run import RunRecord
from app.models.wikidata_studio_cache import WikidataStudioCache


@pytest.mark.asyncio
async def test_export_import_override_round_trip(sample_run, db_session) -> None:
    client = sample_run["client"]
    run_id = sample_run["run_id"]

    db_session.add(
        WikidataStudioCache(
            run_id=run_id,
            approved_only=True,
            input_fingerprint="fp-test",
            result_items=[
                {
                    "local_id": "990001234",
                    "entity_type": "manuscript",
                    "labels": {"en": "MS 1234"},
                    "statements": [],
                    "validation_issues": [],
                },
            ],
            quickstatements="",
            summary={
                "total_items": 1,
                "manuscripts": 1,
                "persons": 0,
                "works": 0,
                "statements": 0,
            },
            approved_match_count=0,
            pending_match_count=0,
            used_match_count=0,
            record_count=1,
        )
    )
    await db_session.commit()

    export = await client.get(
        f"/api/runs/{run_id}/wikidata-studio/items/export?format=json",
    )
    assert export.status_code == 200
    payload = json.loads(export.content)
    assert payload["items"][0]["local_id"] == "990001234"

    bundle = {
        "items": [
            {
                "local_id": "990001234",
                "labels": {"en": "Curator title"},
                "approved": True,
            },
        ],
    }
    imp = await client.post(
        f"/api/runs/{run_id}/wikidata-studio/items/import",
        files={"file": ("items.json", json.dumps(bundle).encode(), "application/json")},
    )
    assert imp.status_code == 200
    body = imp.json()
    assert body["imported"] == 1
    assert body["skipped"] == 0

    row = (
        await db_session.execute(
            WikidataItemOverride.__table__.select().where(
                WikidataItemOverride.run_id == run_id,
                WikidataItemOverride.local_id == "990001234",
            )
        )
    ).mappings().first()
    assert row is not None
    assert row["labels"]["en"] == "Curator title"
    assert row["approved"] is True


@pytest.mark.asyncio
async def test_csv_export_includes_prompt_context_and_full_verdict(sample_run, db_session) -> None:
    client = sample_run["client"]
    run_id = sample_run["run_id"]
    db_session.add(RunRecord(
        run_id=run_id,
        control_number="csv-cn-1",
        marc={"_control_number": "csv-cn-1", "title": "MARC title", "authors": ["Author"]},
    ))
    db_session.add(WikidataStudioCache(
        run_id=run_id,
        approved_only=True,
        input_fingerprint="fp-csv",
        result_items=[{
            "local_id": "csv-item",
            "entity_type": "work",
            "records": ["csv-cn-1"],
            "authority_evidence": [{"source": "NLI", "birth_year": 1950}],
            "local_reference_targets": {"person::1": {"entity_type": "person"}},
            "labels": {"en": "Generated work", "he": "יצירה"},
            "descriptions": {"en": "Generated description"},
            "aliases": {"en": ["Alias"]},
            "statements": [{"property_id": "P31", "value": "Q47461344"}],
            "validation_issues": [],
        }],
        quickstatements="",
        summary={"total_items": 1, "manuscripts": 0, "persons": 0, "works": 1, "statements": 1},
        approved_match_count=1,
        pending_match_count=0,
        used_match_count=1,
        record_count=1,
    ))
    db_session.add(WikidataItemOverride(
        run_id=run_id,
        local_id="csv-item",
        approved=True,
        ai_verdict={
            "overall": "partial",
            "name_ok": "yes",
            "type_ok": "yes",
            "role_ok": "partial",
            "reasoning": "Statement needs evidence",
            "model": "moonshotai/Kimi-K2.5",
        },
    ))
    await db_session.commit()

    response = await client.get(
        f"/api/runs/{run_id}/wikidata-studio/items/export?format=csv",
    )

    assert response.status_code == 200, response.text
    rows = list(csv.DictReader(response.content.decode("utf-8-sig").splitlines()))
    assert len(rows) == 1
    row = rows[0]
    assert row["description_en"] == "Generated description"
    assert json.loads(row["authority_evidence_json"])[0]["source"] == "NLI"
    assert "person::1" in row["local_reference_targets_json"]
    assert "P31" in row["statements_json"]
    assert "MARC title" in row["marc_context_json"]
    assert json.loads(row["ai_verdict_json"])["reasoning"] == "Statement needs evidence"
    assert row["ai_verdict_overall"] == "partial"
