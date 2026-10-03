"""Review-table row page replays the verdict staleness contract (W-268).

A stored verdict whose claims fingerprint no longer matches the row's
current SHA reads as unknown in the fast row path, exactly like the
merged view (Rule W-169) — a rebuild that changes claims must never keep
serving the pre-rebuild verdict.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def stale_verdict_row(db_session, auth_user):
    from app.models.item_override import WikidataItemOverride
    from app.models.project import PROJECT_ROLE_OWNER, Membership, Project
    from app.models.run import RUN_STATUS_SUCCEEDED, Run, RunRecord
    from app.models.wikidata_studio_cache import WikidataStudioCache
    from app.pipeline.wikidata_item_row_views import replace_wikidata_item_rows

    user, _client = auth_user
    project = Project(owner_id=user.id, name="Stale verdict", description="")
    db_session.add(project)
    await db_session.flush()
    db_session.add(Membership(project_id=project.id, user_id=user.id, role=PROJECT_ROLE_OWNER))
    run = Run(
        project_id=project.id, created_by=user.id,
        name="stale-verdict-run", status=RUN_STATUS_SUCCEEDED,
        record_count=1, match_count=1,
    )
    db_session.add(run)
    await db_session.flush()
    marc = {"control_number": "990001761710205171", "title": "a"}
    db_session.add(RunRecord(run_id=run.id, control_number="990001761710205171", marc=marc))
    item = {
        "local_id": "ms:1", "entity_type": "manuscript",
        "labels": {"he": "כתב יד"}, "records": ["990001761710205171"],
        "statements": [{"property_id": "P1684", "value": "old claim",
                        "value_type": "monolingualtext"}],
    }
    db_session.add(WikidataStudioCache(
        run_id=run.id, approved_only=True, source="canonical",
        input_fingerprint="fp", result_items=[dict(item)],
        summary={}, record_count=1,
    ))
    await db_session.commit()
    await replace_wikidata_item_rows(
        db_session, run.id, approved_only=True, source="canonical", items=[dict(item)],
    )
    db_session.add(WikidataItemOverride(
        run_id=run.id, local_id="ms:1", approved=None,
        ai_verdict={"overall": "partial", "name_ok": "yes", "type_ok": "yes",
                    "role_ok": "partial", "reasoning": "r",
                    "cache_key": "stale-sha-from-before-the-rebuild",
                    "judged_at": "2026-10-01T09:16:06+00:00"},
    ))
    await db_session.commit()
    return run.id


@pytest.mark.asyncio
async def test_row_page_drops_a_verdict_whose_sha_no_longer_matches(
    stale_verdict_row, db_session,
):
    from app.pipeline.wikidata_item_row_views import page_wikidata_items

    run_id = stale_verdict_row
    page = await page_wikidata_items(
        db_session, run_id, approved_only=True, source="canonical", limit=10,
    )
    verdicts = [it.get("ai_verdict") for it in page["items"]]
    assert any(v is None for v in verdicts), verdicts


@pytest.mark.asyncio
async def test_row_page_keeps_a_verdict_whose_sha_matches(
    stale_verdict_row, db_session,
):
    """A freshly verified item keeps its verdict — no over-drop."""
    from app.pipeline.wikidata_verdict_cache import wikidata_verdict_input_fingerprint
    from app.models.item_override import WikidataItemOverride
    from sqlalchemy import select

    run_id = stale_verdict_row
    ov = (await db_session.execute(
        select(WikidataItemOverride).where(
            WikidataItemOverride.run_id == run_id,
            WikidataItemOverride.local_id == "ms:1",
        )
    )).scalar_one()
    item = {
        "local_id": "ms:1", "entity_type": "manuscript",
        "labels": {"he": "כתב יד"}, "records": ["990001761710205171"],
        "statements": [{"property_id": "P1684", "value": "old claim",
                        "value_type": "monolingualtext"}],
    }
    from app.pipeline.wikidata_verdict_cache import (
        wikidata_verdict_stable_input_fingerprint,
    )
    from app.pipeline.wikidata_item_views import slim_item_for_verdict_persist
    stable = slim_item_for_verdict_persist(dict(item))
    ov.ai_verdict = {
        **ov.ai_verdict,
        "cache_key": "from-a-fresh-verify",
        "stable_cache_key": wikidata_verdict_stable_input_fingerprint(
            stable, "gemini-3.5-flash",
        ),
    }
    await db_session.commit()

    from app.pipeline.wikidata_item_row_views import page_wikidata_items

    page = await page_wikidata_items(
        db_session, run_id, approved_only=True, source="canonical", limit=10,
    )
    verdicts = [it.get("ai_verdict") for it in page["items"]]
    assert any(v is not None for v in verdicts), verdicts