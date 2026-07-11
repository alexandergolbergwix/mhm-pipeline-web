"""Export/import round-trip for Wikidata Studio item overrides."""

from __future__ import annotations

import json

import pytest

from app.models.item_override import WikidataItemOverride
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
