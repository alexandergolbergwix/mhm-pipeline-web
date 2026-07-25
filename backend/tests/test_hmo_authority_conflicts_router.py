"""Router tests for HMO Studio authority-conflict resolver."""

from __future__ import annotations

import uuid

import pytest

from app.models.run import AuthorityMatch


@pytest.mark.asyncio
async def test_get_authority_conflicts_lists_colliding_owners(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    a = uuid.uuid4()
    b = uuid.uuid4()
    db_session.add_all([
        AuthorityMatch(
            id=a, run_id=run_id, control_number="1", entity_text="Person A",
            entity_kind="person", role="author", approved=True,
            wikidata_qid="Q42", confidence="high", source="test",
        ),
        AuthorityMatch(
            id=b, run_id=run_id, control_number="2", entity_text="Person B",
            entity_kind="person", role="author", approved=True,
            wikidata_qid="Q42", confidence="medium", source="test",
        ),
    ])
    await db_session.commit()

    response = await sample_run["client"].get(
        f"/api/runs/{run_id}/hmo-studio/authority-conflicts",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert body["conflict_count"] == 1
    assert body["conflicts"][0]["identifier"] == "Q42"
    owner_ids = {o["match_id"] for o in body["conflicts"][0]["owners"]}
    assert owner_ids == {str(a), str(b)}


@pytest.mark.asyncio
async def test_resolve_keeps_one_and_unapproves_rest(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    a = uuid.uuid4()
    b = uuid.uuid4()
    db_session.add_all([
        AuthorityMatch(
            id=a, run_id=run_id, control_number="1", entity_text="Person A",
            entity_kind="person", role="author", approved=True,
            mazal_id="987007111", confidence="high", source="test",
        ),
        AuthorityMatch(
            id=b, run_id=run_id, control_number="2", entity_text="Person B",
            entity_kind="person", role="author", approved=True,
            mazal_id="987007111", confidence="low", source="test",
        ),
    ])
    await db_session.commit()

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/authority-conflicts/resolve",
        json={"keep_match_ids": [str(a)], "unapprove_match_ids": []},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert str(b) in body["unapproved_match_ids"]
    assert str(a) not in body["unapproved_match_ids"]

    db_session.expire_all()
    kept = await db_session.get(AuthorityMatch, a)
    dropped = await db_session.get(AuthorityMatch, b)
    assert kept is not None and kept.approved is True
    assert dropped is not None and dropped.approved is False


@pytest.mark.asyncio
async def test_resolve_requires_ids(sample_run) -> None:
    run_id = sample_run["run_id"]
    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/authority-conflicts/resolve",
        json={},
    )
    assert response.status_code == 400
