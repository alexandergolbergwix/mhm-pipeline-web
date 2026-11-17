import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event

from app.models.item_override import WikidataItemOverride
from app.models.wikidata_studio_cache import WikidataStudioCache
from app.models.wikidata_studio_item_row import WikidataStudioItemRow
from app.routers.wikidata_repair_plan import qid_edit_indices, validation_codes


@pytest_asyncio.fixture(autouse=True)
async def administrator(sample_run, db_session):
    from app.models.user import User
    user = await db_session.get(User, sample_run["user_id"])
    user.role = "admin"
    await db_session.commit()


async def seed(db, run_id):
    cache = WikidataStudioCache(
        run_id=run_id,
        source="canonical",
        approved_only=True,
        input_fingerprint="f" * 64,
    )
    db.add(cache)
    for local_id in ("a", "b", "c"):
        db.add(
            WikidataStudioItemRow(
                run_id=run_id,
                source="canonical",
                approved_only=True,
                local_id=local_id,
                label_en=f"Item {local_id}",
                existing_qid="Q123",
                payload={
                    "validation_issues": [{"code": "CATALOG_CODE_IS_QID"}],
                    "private_bulk": "excluded",
                },
            )
        )
    db.add(
        WikidataItemOverride(
            run_id=run_id,
            local_id="a",
            statement_edits={"16": {"value": "Q456"}},
            approved=False,
        )
    )
    await db.commit()
    return cache


@pytest.mark.asyncio
async def test_inventory_is_bounded_read_only_and_stays_in_its_scope(sample_run, db_session):
    client, run_id = sample_run["client"], sample_run["run_id"]
    await seed(db_session, run_id)
    # Another run and another build mode must not enter the page.
    for other_run, source in ((uuid.uuid4(), "canonical"), (run_id, "legacy")):
        db_session.add(
            WikidataStudioItemRow(
                run_id=other_run,
                source=source,
                approved_only=True,
                local_id="foreign-row",
            )
        )
    await db_session.commit()
    queries = []
    engine = db_session.bind.sync_engine

    def record(_connection, _cursor, statement, *_args):
        queries.append(statement.lower())

    event.listen(engine, "before_cursor_execute", record)
    try:
        response = await client.get(f"/api/runs/{run_id}/wikidata-studio/repair-plan?page_size=2")
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "read_only"
    assert body["public_verification"] == "not_checked"
    assert body["total_items"] == 3
    assert [item["local_id"] for item in body["items"]] == ["a", "b"]
    assert body["items"][0]["qid_edit_indices"] == ["16"]
    assert body["items"][0]["approved"] is False
    assert body["items"][0]["validation_codes"] == ["CATALOG_CODE_IS_QID"]
    assert "private_bulk" not in response.text
    assert not any("result_items" in query for query in queries)
    assert not any(
        query.lstrip().startswith(("insert", "update", "delete")) and "wikidata" in query
        for query in queries
    )
    next_page = await client.get(
        f"/api/runs/{run_id}/wikidata-studio/repair-plan",
        params={
            "cursor": body["next_cursor"],
            "expected_build_token": body["build_token"],
            "page_size": 2,
        },
    )
    assert next_page.status_code == 200
    assert [item["local_id"] for item in next_page.json()["items"]] == ["c"]
    assert next_page.json()["next_cursor"] is None


@pytest.mark.asyncio
async def test_rebuild_invalidates_the_cursor_even_when_the_cache_id_stays_same(
    sample_run, db_session
):
    client, run_id = sample_run["client"], sample_run["run_id"]
    cache = await seed(db_session, run_id)
    path = f"/api/runs/{run_id}/wikidata-studio/repair-plan"
    body = (await client.get(path)).json()
    cache.built_at = datetime.now(UTC) + timedelta(seconds=1)
    await db_session.commit()
    response = await client.get(path, params={"expected_build_token": body["build_token"]})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_inventory_requires_project_access_and_has_no_execution_method(
    sample_run, db_session, async_client
):
    client, run_id = sample_run["client"], sample_run["run_id"]
    await seed(db_session, run_id)
    path = f"/api/runs/{run_id}/wikidata-studio/repair-plan"
    assert (
        await client.get(f"/api/runs/{uuid.uuid4()}/wikidata-studio/repair-plan")
    ).status_code == 404
    assert (await client.post(path, json={"execute": True})).status_code == 405
    async_client.cookies.clear()
    assert (await async_client.get(path)).status_code == 401


@pytest.mark.asyncio
async def test_inventory_validates_page_limits_and_requires_a_saved_build(sample_run):
    client, run_id = sample_run["client"], sample_run["run_id"]
    path = f"/api/runs/{run_id}/wikidata-studio/repair-plan"
    assert (await client.get(path, params={"page_size": 101})).status_code == 422
    assert (await client.get(path, params={"page_size": 0})).status_code == 422
    assert (await client.get(path)).status_code == 409


def test_old_partial_override_shapes_do_not_create_false_proof():
    assert qid_edit_indices({"0": None, "1": {"value": " Q123 "}, "2": {"value": 123}}) == ["1"]
    assert qid_edit_indices(None) == []
    assert validation_codes([None, {"code": 42}, {"code": "X"}, {"code": "X"}]) == ["X"]
