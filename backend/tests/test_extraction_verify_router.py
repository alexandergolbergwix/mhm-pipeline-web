"""Tests for the ``/runs/{id}/extraction/ai-verify/*`` endpoint family.

Pins three contracts:

1. ``GET /actions?scope_kind=all`` returns ``audit_ner_extraction`` with
   its four evaluators visible.
2. ``POST /start-stream`` with no Gemini key configured returns 400
   with a friendly message.
3. ``_persist_ai_verdicts_to_entities`` writes the verdict summary
   into ``ExtractionApproval.ai_verdict`` keyed by ``_entity_id``.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select


@pytest_asyncio.fixture
async def sample_extraction_run(db_session, sample_run):
    """Build on top of ``sample_run`` by seeding one ExtractionApproval row."""
    from app.models.extraction_approval import ExtractionApproval

    ext = ExtractionApproval(
        run_id=sample_run["run_id"],
        control_number=sample_run["control_number"],
        source="person_ner",
        text="Maimonides",
        start=0,
        end=10,
        type="PERSON",
        role="author",
        confidence=0.85,
        model_confidence=0.92,
        approved=False,
    )
    db_session.add(ext)
    await db_session.commit()
    return {**sample_run, "entity_id": ext.id}


class TestActionsEndpoint:
    @pytest.mark.asyncio
    async def test_scope_all_returns_audit_ner_extraction(
        self, sample_extraction_run,
    ) -> None:
        client = sample_extraction_run["client"]
        run_id = sample_extraction_run["run_id"]
        r = await client.get(
            f"/api/runs/{run_id}/extraction/ai-verify/actions?scope_kind=all",
        )
        assert r.status_code == 200
        actions = r.json()
        by_id = {a["id"]: a for a in actions}
        assert "audit_ner_extraction" in by_id
        audit = by_id["audit_ner_extraction"]
        assert set(audit["evaluators"]) == {
            "person_ner", "provenance_ner",
            "contents_ner", "genre_classifier",
        }
        assert audit["min_candidates"] >= 1

    @pytest.mark.asyncio
    async def test_invalid_scope_kind_rejected(
        self, sample_extraction_run,
    ) -> None:
        client = sample_extraction_run["client"]
        run_id = sample_extraction_run["run_id"]
        r = await client.get(
            f"/api/runs/{run_id}/extraction/ai-verify/actions?scope_kind=garbage",
        )
        assert r.status_code == 422


class TestStartStreamNoGeminiKey:
    @pytest.mark.asyncio
    async def test_start_stream_returns_400_when_no_gemini_key(
        self, sample_extraction_run, monkeypatch,
    ) -> None:
        client = sample_extraction_run["client"]
        run_id = sample_extraction_run["run_id"]

        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        r = await client.post(
            f"/api/runs/{run_id}/extraction/ai-verify/start-stream",
            json={"action_id": "audit_ner_extraction"},
        )
        assert r.status_code == 400
        detail = r.json().get("detail", "")
        assert "Gemini" in detail


class TestPersistAiVerdictsToEntities:
    @pytest.mark.asyncio
    async def test_verdict_persisted_to_extraction_approval_row(
        self, db_session, sample_extraction_run,
    ) -> None:
        from app.models.extraction_approval import ExtractionApproval
        from app.routers.extraction_verify import (
            _persist_ai_verdicts_to_entities,
        )

        ext_id = sample_extraction_run["entity_id"]
        run_id = sample_extraction_run["run_id"]

        verdicts = [
            {
                "candidate": {"_entity_id": str(ext_id)},
                "verdict": {
                    "overall":   "pass",
                    "name_ok":   True,
                    "type_ok":   True,
                    "role_ok":   True,
                    "reasoning": "Looks right — clearly Maimonides.",
                },
                "judge_id":     "gemini-3.5-flash",
                "judged_at":    "2026-06-01T00:00:00Z",
                "cache_key":    "abc",
                "evaluator_id": "person_ner",
            },
        ]

        await _persist_ai_verdicts_to_entities(
            run_id=str(run_id),
            session_id="20260601T000000Z",
            verdicts=verdicts,
        )

        ext = (
            await db_session.execute(
                select(ExtractionApproval).where(
                    ExtractionApproval.id == ext_id,
                )
            )
        ).scalar_one()
        await db_session.refresh(ext)
        assert ext.ai_verdict is not None
        assert ext.ai_verdict["overall"] == "pass"
        assert ext.ai_verdict["evaluator"] == "person_ner"
        assert ext.ai_verdict["model"] == "gemini-3.5-flash"
        assert ext.ai_verdict["session_id"] == "20260601T000000Z"
        assert ext.ai_verdict_at is not None

    @pytest.mark.asyncio
    async def test_verdict_missing_entity_id_is_silently_skipped(
        self, db_session, sample_extraction_run,
    ) -> None:
        """A malformed verdict must not raise — otherwise session.end never fires."""
        from app.routers.extraction_verify import (
            _persist_ai_verdicts_to_entities,
        )

        run_id = sample_extraction_run["run_id"]
        await _persist_ai_verdicts_to_entities(
            run_id=str(run_id),
            session_id="20260601T000000Z",
            verdicts=[
                {"candidate": {}, "verdict": {"overall": "fail"}},
                {"candidate": {"_entity_id": "not-a-uuid"},
                 "verdict": {"overall": "fail"}},
            ],
        )
