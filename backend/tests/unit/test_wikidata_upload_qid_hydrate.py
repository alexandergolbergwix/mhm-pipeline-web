"""Rule W-177 — upload natives must carry Studio-cache adopted QIDs.

Export-37 on run 48ba6c13 showed 13 on-Wikidata UPDATEs, but upload SPARQL-
reconciled them as CREATE because ``_build_native_items`` re-projected from HMO
and dropped the build's W-168 ``existing_qid``.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.models.wikidata_studio_cache import WikidataStudioCache
from app.routers.wikidata_studio import _apply_cached_qid_adoption_to_native


@pytest.mark.asyncio
async def test_upload_natives_receive_studio_cache_existing_qid(db_session, monkeypatch) -> None:
    run_id = uuid.uuid4()
    db_session.add(
        WikidataStudioCache(
            run_id=run_id,
            approved_only=True,
            source="canonical",
            input_fingerprint="f" * 64,
            result_items=[
                {
                    "local_id": "QDraft_Person_130",
                    "entity_type": "person",
                    "existing_qid": "Q55913805",
                    "labels": {"he": "משה"},
                    "statements": [],
                },
                {
                    "local_id": "QDraft_MS_1",
                    "entity_type": "manuscript",
                    "existing_qid": "Q134603946",
                    "labels": {"en": "Ms. Heb. 1"},
                    "statements": [],
                },
            ],
            quickstatements="",
            summary={"total_items": 2},
            approved_match_count=0,
            pending_match_count=0,
            used_match_count=0,
            record_count=2,
        )
    )
    await db_session.commit()

    async def _no_probe(*_a, **_k) -> int:
        return 0

    monkeypatch.setattr(
        "app.routers.wikidata_studio._adopt_probed_duplicate_qids",
        _no_probe,
    )

    natives = [
        SimpleNamespace(local_id="QDraft_Person_130", existing_qid=None, statements=[]),
        SimpleNamespace(local_id="QDraft_MS_1", existing_qid=None, statements=[]),
        SimpleNamespace(local_id="QDraft_Person_new", existing_qid=None, statements=[]),
    ]
    stamped = await _apply_cached_qid_adoption_to_native(
        db_session,
        run_id,
        approved_only=True,
        source="canonical",
        native=natives,
    )
    assert stamped == 2
    assert natives[0].existing_qid == "Q55913805"
    assert natives[1].existing_qid == "Q134603946"
    assert natives[2].existing_qid is None


@pytest.mark.asyncio
async def test_upload_hydrate_does_not_overwrite_native_qid(db_session, monkeypatch) -> None:
    run_id = uuid.uuid4()
    db_session.add(
        WikidataStudioCache(
            run_id=run_id,
            approved_only=True,
            source="canonical",
            input_fingerprint="f" * 64,
            result_items=[
                {
                    "local_id": "QDraft_Person_130",
                    "entity_type": "person",
                    "existing_qid": "Q111",
                    "statements": [],
                },
            ],
            quickstatements="",
            summary={},
            approved_match_count=0,
            pending_match_count=0,
            used_match_count=0,
            record_count=1,
        )
    )
    await db_session.commit()

    async def _no_probe(*_a, **_k) -> int:
        return 0

    monkeypatch.setattr(
        "app.routers.wikidata_studio._adopt_probed_duplicate_qids",
        _no_probe,
    )

    natives = [
        SimpleNamespace(local_id="QDraft_Person_130", existing_qid="Q222", statements=[]),
    ]
    await _apply_cached_qid_adoption_to_native(
        db_session,
        run_id,
        approved_only=True,
        source="canonical",
        native=natives,
    )
    assert natives[0].existing_qid == "Q222"
