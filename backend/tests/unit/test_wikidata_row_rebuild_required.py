"""Review-table row page sets rebuild_required like the merged view (Rule W-268).

A content override newer than the cached build flags the row for rebuild.
The row page uses the same helper as the merged view, so both paths agree.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

BUILT_AT = datetime(2026, 11, 1, tzinfo=UTC)
ITEMS = [
    {"local_id": "ms:edited", "entity_type": "manuscript", "labels": {"he": "א"}, "statements": []},
    {"local_id": "ms:untouched", "entity_type": "manuscript", "labels": {"he": "ב"}, "statements": []},
]


@pytest_asyncio.fixture
async def built_run_with_newer_override(db_session, auth_user):
    from app.models.item_override import WikidataItemOverride
    from app.models.project import PROJECT_ROLE_OWNER, Membership, Project
    from app.models.run import RUN_STATUS_SUCCEEDED, Run
    from app.models.wikidata_studio_cache import WikidataStudioCache
    from app.pipeline.wikidata_item_row_views import replace_wikidata_item_rows

    user, _client = auth_user
    project = Project(owner_id=user.id, name="Rebuild flag", description="")
    db_session.add(project)
    await db_session.flush()
    db_session.add(Membership(project_id=project.id, user_id=user.id, role=PROJECT_ROLE_OWNER))
    run = Run(
        project_id=project.id, created_by=user.id,
        name="rebuild-required-run", status=RUN_STATUS_SUCCEEDED,
        record_count=2, match_count=2,
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(WikidataStudioCache(
        run_id=run.id, approved_only=True, source="canonical",
        input_fingerprint="fp-rebuild", result_items=[dict(item) for item in ITEMS],
        summary={"overrides_applied": True}, record_count=2, built_at=BUILT_AT,
    ))
    db_session.add(WikidataItemOverride(
        run_id=run.id, local_id="ms:edited",
        labels={"he": "א שונה"},
        updated_at=BUILT_AT + timedelta(hours=1),
    ))
    await db_session.commit()
    await replace_wikidata_item_rows(
        db_session, run.id, approved_only=True, source="canonical",
        items=[dict(item) for item in ITEMS],
    )
    return run.id


@pytest.mark.asyncio
async def test_row_page_flags_only_the_item_with_a_newer_content_override(
    built_run_with_newer_override, db_session,
):
    from app.pipeline.wikidata_item_row_views import page_wikidata_items

    page = await page_wikidata_items(
        db_session, built_run_with_newer_override,
        approved_only=True, source="canonical", limit=10,
    )
    flags = {item["local_id"]: item["rebuild_required"] for item in page["items"]}
    assert flags == {"ms:edited": True, "ms:untouched": False}


@pytest.mark.asyncio
async def test_row_page_matches_the_merged_view_flag(
    built_run_with_newer_override, db_session,
):
    from app.pipeline.wikidata_item_row_views import page_wikidata_items
    from app.pipeline.wikidata_item_views import fetch_merged_wikidata_items

    run_id = built_run_with_newer_override
    page = await page_wikidata_items(
        db_session, run_id, approved_only=True, source="canonical", limit=10,
    )
    merged = await fetch_merged_wikidata_items(
        db_session, run_id, approved_only=True, source="canonical",
    )
    page_flags = {item["local_id"]: item["rebuild_required"] for item in page["items"]}
    merged_flags = {item["local_id"]: item["rebuild_required"] for item in merged}
    assert page_flags == merged_flags
