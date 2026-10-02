"""All-matches review-table fallback — the false scope reads the approved build."""

from __future__ import annotations

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def approved_build(db_session, auth_user):
    from app.models.project import PROJECT_ROLE_OWNER, Membership, Project
    from app.models.run import RUN_STATUS_SUCCEEDED, Run
    from app.models.wikidata_studio_cache import WikidataStudioCache

    user, _client = auth_user
    project = Project(owner_id=user.id, name="Row fallback", description="")
    db_session.add(project)
    await db_session.flush()
    db_session.add(Membership(project_id=project.id, user_id=user.id, role=PROJECT_ROLE_OWNER))
    run = Run(
        project_id=project.id, created_by=user.id,
        name="row-fallback-run", status=RUN_STATUS_SUCCEEDED,
        record_count=1, match_count=1,
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(WikidataStudioCache(
        run_id=run.id, approved_only=True, source="canonical",
        input_fingerprint="fp-approved", result_items=[
            {"local_id": "ms:1", "entity_type": "manuscript", "labels": {"he": "כתב יד"}},
        ],
        summary={}, record_count=1,
    ))
    await db_session.commit()
    return run.id


@pytest.mark.asyncio
async def test_all_matches_scope_falls_back_to_the_approved_build(
    approved_build, db_session,
):
    from app.pipeline.wikidata_item_row_views import ensure_rows_backfilled

    run_id = approved_build
    count = await ensure_rows_backfilled(
        db_session, run_id, approved_only=False, source="canonical",
    )
    assert count == 1


@pytest.mark.asyncio
async def test_missing_build_for_a_run_still_raises(approved_build, db_session):
    import uuid

    from app.pipeline.wikidata_item_row_views import ensure_rows_backfilled

    with pytest.raises(LookupError):
        await ensure_rows_backfilled(
            db_session, uuid.uuid4(), approved_only=False, source="canonical",
        )
