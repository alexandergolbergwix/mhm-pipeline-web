"""Cache invalidation for extraction entity AI verdicts."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.extraction_approval import ExtractionApproval
from app.pipeline.ner_verdict_cache import (
    entity_dict_verdict_fingerprint,
    ner_verdict_input_fingerprint,
    sanitise_stale_ai_verdict,
)


@pytest_asyncio.fixture
async def extraction_entity(db_session, sample_run):
    ext = ExtractionApproval(
        run_id=sample_run["run_id"],
        control_number=sample_run["control_number"],
        source="person_ner",
        text="Maimonides",
        start=0,
        end=10,
        type="PERSON",
        role="AUTHOR",
        confidence=0.85,
        model_confidence=0.92,
        approved=False,
        ai_verdict={
            "overall": "pass",
            "model": "gemini-3.5-flash",
            "cache_key": "stale-key",
        },
    )
    db_session.add(ext)
    await db_session.commit()
    return {**sample_run, "entity": ext}


class TestSanitiseStaleAiVerdict:
    def test_matching_cache_key_is_kept(self, extraction_entity) -> None:
        ext = extraction_entity["entity"]
        fp = ner_verdict_input_fingerprint(ext)
        ent = {
            "control_number": ext.control_number,
            "source": ext.source,
            "start": ext.start,
            "end": ext.end,
            "text": ext.text,
            "type": ext.type,
            "role": ext.role,
            "ai_verdict": {"overall": "pass", "cache_key": fp, "model": "gemini-3.5-flash"},
        }
        out = sanitise_stale_ai_verdict(ent)
        assert out is not None
        assert out["overall"] == "pass"

    def test_mismatched_cache_key_is_dropped(self, extraction_entity) -> None:
        ext = extraction_entity["entity"]
        ent = {
            "control_number": ext.control_number,
            "source": ext.source,
            "start": ext.start,
            "end": ext.end,
            "text": ext.text,
            "type": ext.type,
            "role": ext.role,
            "ai_verdict": {"overall": "pass", "cache_key": "wrong", "model": "gemini-3.5-flash"},
        }
        assert sanitise_stale_ai_verdict(ent) is None

    def test_override_text_change_changes_fingerprint(self, extraction_entity) -> None:
        ext = extraction_entity["entity"]
        base = {
            "control_number": ext.control_number,
            "source": ext.source,
            "start": ext.start,
            "end": ext.end,
            "text": ext.text,
            "type": ext.type,
            "role": ext.role,
        }
        fp1 = entity_dict_verdict_fingerprint(base)
        fp2 = entity_dict_verdict_fingerprint({**base, "override_text": "Rambam"})
        assert fp1 != fp2


class TestPatchClearsAiVerdict:
    @pytest.mark.asyncio
    async def test_patch_override_text_clears_verdict(
        self, db_session, extraction_entity,
    ) -> None:
        from app.routers.extraction import _entity_id

        ext = extraction_entity["entity"]
        run_id = extraction_entity["run_id"]
        client = extraction_entity["client"]

        entity_id = _entity_id(
            control_number=ext.control_number,
            source=ext.source,
            text=ext.text,
            start=int(ext.start or 0),
            end=int(ext.end or 0),
        )

        r = await client.patch(
            f"/api/runs/{run_id}/extraction/entities/{entity_id}",
            json={"override_text": "Rambam"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("ai_verdict") is None

        row = (
            await db_session.execute(
                select(ExtractionApproval).where(ExtractionApproval.id == ext.id)
            )
        ).scalar_one()
        await db_session.refresh(row)
        assert row.ai_verdict is None
        assert row.ai_verdict_at is None
