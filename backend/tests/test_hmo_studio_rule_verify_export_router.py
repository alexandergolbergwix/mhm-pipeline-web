"""Router tests for GET /runs/{run_id}/hmo-studio/items/rule-verify/export."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid

from app.models.hmo_studio_item_cache import HmoStudioItemCache
from app.models.hmo_studio_item_override import HmoStudioItemOverride
from converter.wikibase.resolved_models import ResolvedWikibaseEntity

_VERDICT_FAILING = {
    "overall": "fail",
    "results": [
        {"rule_id": "R_label", "state": "fail", "field": "label", "message": "bad label"},
        {"rule_id": "R_desc", "state": "pass"},
    ],
}
_VERDICT_ERROR_ONLY = {
    "overall": "error",
    "results": [
        {"rule_id": "R_api", "state": "error", "message": "api down"},
    ],
}


async def _seed(db_session, run_id, entities) -> None:
    db_session.add(
        HmoStudioItemCache(
            run_id=run_id,
            input_fingerprint="0" * 64,
            resolved_entities=entities,
            entity_count=len(entities),
        )
    )
    await db_session.flush()
    for i, entity in enumerate(entities):
        db_session.add(
            HmoStudioItemOverride(
                run_id=run_id,
                local_id=entity["local_id"],
                rule_verdict=_VERDICT_FAILING if i == 0 else _VERDICT_ERROR_ONLY,
            )
        )
    await db_session.commit()


async def test_export_returns_409_without_build(sample_run) -> None:
    run_id = sample_run["run_id"]
    response = await sample_run["client"].get(
        f"/api/runs/{run_id}/hmo-studio/items/rule-verify/export"
    )
    assert response.status_code == 409


async def test_export_json_streams_entities(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    entity = ResolvedWikibaseEntity(
        local_id="QDraft_MS1",
        labels={"en": "Test MS"},
        descriptions={"en": "a manuscript"},
        class_qid="Q1",
        source_uri="http://example.org#MS1",
    )
    await _seed(db_session, run_id, [entity.to_dict()])

    response = await sample_run["client"].get(
        f"/api/runs/{run_id}/hmo-studio/items/rule-verify/export?format=json"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]
    body = response.json()
    assert body["run_id"] == str(run_id)
    assert body["scope"] == "failures"
    assert len(body["entities"]) == 1
    row = body["entities"][0]
    assert row["local_id"] == "QDraft_MS1"
    assert row["label"] == "Test MS"
    assert row["overall"] == "fail"
    assert row["pass_count"] == 1
    assert [r["rule_id"] for r in row["results"]] == ["R_label"]
    assert row["entity"]["labels"] == {"en": "Test MS"}


async def test_export_json_scope_all_includes_error_only(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    entities = [
        ResolvedWikibaseEntity(
            local_id="QDraft_MS1",
            labels={"en": "Test MS"},
            descriptions={"en": "first"},
            class_qid="Q1",
            source_uri="http://example.org#MS1",
        ).to_dict(),
        ResolvedWikibaseEntity(
            local_id="QDraft_MS2",
            labels={"en": "Second MS"},
            descriptions={"en": "second"},
            class_qid="Q1",
            source_uri="http://example.org#MS2",
        ).to_dict(),
    ]
    await _seed(db_session, run_id, entities)

    failures = await sample_run["client"].get(
        f"/api/runs/{run_id}/hmo-studio/items/rule-verify/export?format=json&scope=failures"
    )
    assert [e["local_id"] for e in failures.json()["entities"]] == ["QDraft_MS1"]

    all_scope = await sample_run["client"].get(
        f"/api/runs/{run_id}/hmo-studio/items/rule-verify/export?format=json&scope=all"
    )
    assert [e["local_id"] for e in all_scope.json()["entities"]] == ["QDraft_MS1", "QDraft_MS2"]


async def test_export_csv_one_row_per_non_pass_result(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    entities = [
        ResolvedWikibaseEntity(
            local_id=f"QDraft_MS{i + 1}",
            labels={"en": f"MS{i + 1}"},
            descriptions={"en": f"manuscript {i + 1}"},
            class_qid="Q1",
            source_uri=f"http://example.org#MS{i + 1}",
        ).to_dict()
        for i in range(2)
    ]
    await _seed(db_session, run_id, entities)

    response = await sample_run["client"].get(
        f"/api/runs/{run_id}/hmo-studio/items/rule-verify/export?format=csv&scope=all"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.text)))
    header, data = rows[0], rows[1:]
    assert header[:3] == ["local_id", "label", "class_qid"]
    assert len(data) == 2
    by_id = {row[0]: row for row in data}
    assert by_id["QDraft_MS1"][header.index("rule_id")] == "R_label"
    assert by_id["QDraft_MS1"][header.index("state")] == "fail"
    assert by_id["QDraft_MS2"][header.index("rule_id")] == "R_api"
    assert by_id["QDraft_MS2"][header.index("state")] == "error"


async def test_export_unknown_run_forbidden(auth_user) -> None:
    _, client = auth_user
    response = await client.get(
        f"/api/runs/{uuid.uuid4()}/hmo-studio/items/rule-verify/export"
    )
    assert response.status_code in (403, 404)


async def test_export_json_keepalive_during_slow_merge(
    sample_run, db_session, monkeypatch
) -> None:
    """A cold-cache merge must stream keepalive bytes, never go silent (H15)."""
    from app.routers import hmo_studio_items as rmod

    run_id = sample_run["run_id"]
    entity = ResolvedWikibaseEntity(
        local_id="QDraft_MS1",
        labels={"en": "Test MS"},
        descriptions={"en": "a manuscript"},
        class_qid="Q1",
        source_uri="http://example.org#MS1",
    )
    await _seed(db_session, run_id, [entity.to_dict()])

    async def slow_merge(db, rid):
        await asyncio.sleep(0.3)
        return [{
            "local_id": "QDraft_MS1",
            "labels": {"en": "Test MS"},
            "class_qid": "Q1",
        }]

    monkeypatch.setattr(rmod, "EXPORT_KEEPALIVE_S", 0.05)
    monkeypatch.setattr(rmod, "fetch_merged_hmo_items_cached", slow_merge)

    response = await sample_run["client"].get(
        f"/api/runs/{run_id}/hmo-studio/items/rule-verify/export?format=json"
    )

    assert response.status_code == 200
    body = response.text
    assert json.loads(body)["entities"][0]["local_id"] == "QDraft_MS1"
    # The keepalive newline lands inside the array, before the first entity.
    assert body.index("\n") < body.index("QDraft_MS1")


async def test_export_csv_keepalive_during_slow_merge(
    sample_run, db_session, monkeypatch
) -> None:
    from app.routers import hmo_studio_items as rmod

    run_id = sample_run["run_id"]
    entity = ResolvedWikibaseEntity(
        local_id="QDraft_MS1",
        labels={"en": "Test MS"},
        descriptions={"en": "a manuscript"},
        class_qid="Q1",
        source_uri="http://example.org#MS1",
    )
    await _seed(db_session, run_id, [entity.to_dict()])

    async def slow_merge(db, rid):
        await asyncio.sleep(0.3)
        return [{
            "local_id": "QDraft_MS1",
            "labels": {"en": "Test MS"},
            "class_qid": "Q1",
        }]

    monkeypatch.setattr(rmod, "EXPORT_KEEPALIVE_S", 0.05)
    monkeypatch.setattr(rmod, "fetch_merged_hmo_items_cached", slow_merge)

    response = await sample_run["client"].get(
        f"/api/runs/{run_id}/hmo-studio/items/rule-verify/export?format=csv"
    )

    assert response.status_code == 200
    body = response.text
    # Blank-line keepalives between the header and the data rows.
    assert "\r\n\r\n" in body
    assert "QDraft_MS1" in body
