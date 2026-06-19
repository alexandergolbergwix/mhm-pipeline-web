"""TDD tests for the suggested_fix extension of the NER AI verify flow.

Pins these contracts:

1.  ``_ner_verdict_query_summary`` includes schema/prompt version keys
    so old cached verdicts (without fixes) can't mask new runs.
2.  ``_persist_ai_verdicts_to_entities`` writes ``suggested_fix`` into
    ``ExtractionApproval.ai_verdict``.
3.  ``_write_ner_verdicts_to_cache`` preserves ``suggested_fix`` in the
    cached payload.
4.  A verdict from the cache that carries ``suggested_fix`` is
    round-tripped intact.
5.  ``_persist_ai_verdicts_to_entities`` with ``suggested_fix=None``
    stores null (not absent) so frontend can distinguish "no fix"
    from "not yet judged".
6.  ``_approval_to_ner_shape`` propagates the entity's ``exists_in``
    status as a ``grounded`` hint so the eval-agent context is richer.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def sample_ner_run(db_session, sample_run):
    from app.models.extraction_approval import ExtractionApproval

    ext = ExtractionApproval(
        run_id=sample_run["run_id"],
        control_number=sample_run["control_number"],
        source="person_ner",
        text="יוסף",
        start=0,
        end=5,
        type="PERSON",
        role="TRANSCRIBER",
        confidence=0.82,
        model_confidence=0.91,
        approved=False,
        exists_in="grounded",
    )
    db_session.add(ext)
    await db_session.commit()
    return {**sample_run, "entity_id": ext.id}


# ── 1. Cache key includes prompt version ─────────────────────────────────

class TestNerVerdictQuerySummaryVersion:
    @pytest.mark.asyncio
    async def test_query_summary_includes_schema_version(
        self, db_session, sample_ner_run,
    ) -> None:
        """The query summary must include the schema/prompt version so old
        cached verdicts (without suggested_fix) cannot be served as
        current-schema hits."""
        from app.models.extraction_approval import ExtractionApproval
        from app.pipeline.ner_verdict_cache import ner_verdict_query_summary

        ext_id = sample_ner_run["entity_id"]
        ext = (
            await db_session.execute(
                select(ExtractionApproval).where(ExtractionApproval.id == ext_id)
            )
        ).scalar_one()

        qs = ner_verdict_query_summary(ext)
        assert "ai_extraction_verdict_schema" in qs
        assert qs["ai_extraction_verdict_schema"] == "v2"
        assert "suggested_fix_policy" in qs

    @pytest.mark.asyncio
    async def test_query_summary_is_deterministic(
        self, db_session, sample_ner_run,
    ) -> None:
        """Same entity produces the same cache key twice."""
        from app.models.extraction_approval import ExtractionApproval
        from app.pipeline.ner_verdict_cache import ner_verdict_query_summary
        from app.pipeline.inference_cache import canonical_hash

        ext_id = sample_ner_run["entity_id"]
        ext = (
            await db_session.execute(
                select(ExtractionApproval).where(ExtractionApproval.id == ext_id)
            )
        ).scalar_one()

        assert canonical_hash(ner_verdict_query_summary(ext)) == \
               canonical_hash(ner_verdict_query_summary(ext))


# ── 2. Persistence writes suggested_fix ──────────────────────────────────

class TestPersistSuggestedFix:
    @pytest.mark.asyncio
    async def test_suggested_fix_persisted_to_extraction_approval(
        self, db_session, sample_ner_run,
    ) -> None:
        from app.models.extraction_approval import ExtractionApproval
        from app.routers.extraction_verify import _persist_ai_verdicts_to_entities

        ext_id = sample_ner_run["entity_id"]
        run_id = sample_ner_run["run_id"]
        ext = (
            await db_session.execute(
                select(ExtractionApproval).where(ExtractionApproval.id == ext_id)
            )
        ).scalar_one()

        verdicts = [
            {
                "candidate": {"_entity_id": str(ext_id)},
                "verdict": {
                    "overall":   "partial",
                    "name_ok":   "partial",
                    "type_ok":   "yes",
                    "role_ok":   "yes",
                    "reasoning": "Truncated name; patronymic in colophon.",
                    "suggested_fix": {
                        "text":         "יוסף בן יעקב",
                        "reasoning":    "Full name in colophon_text.",
                        "source_field": "colophon_text",
                        "confidence":   "high",
                    },
                },
                "judge_id":     "gemini-3.1-pro-preview",
                "judged_at":    "2026-06-07T00:00:00Z",
                "cache_key":    "b" * 64,
                "evaluator_id": "person_ner",
            },
        ]

        await _persist_ai_verdicts_to_entities(
            run_id=str(run_id),
            session_id="20260607T000000Z",
            verdicts=verdicts,
            entities=[ext],
        )

        ext = (
            await db_session.execute(
                select(ExtractionApproval).where(ExtractionApproval.id == ext_id)
            )
        ).scalar_one()
        await db_session.refresh(ext)

        assert ext.ai_verdict is not None
        assert ext.ai_verdict["overall"] == "partial"
        fix = ext.ai_verdict.get("suggested_fix")
        assert fix is not None
        assert fix["text"] == "יוסף בן יעקב"
        assert fix["confidence"] == "high"
        assert fix["source_field"] == "colophon_text"

    @pytest.mark.asyncio
    async def test_null_suggested_fix_stored_as_null(
        self, db_session, sample_ner_run,
    ) -> None:
        """When suggested_fix is None in the verdict, it must be stored
        as null in the JSONB (not missing), so the frontend can distinguish
        "no fix proposed" from "not yet judged"."""
        from app.models.extraction_approval import ExtractionApproval
        from app.routers.extraction_verify import _persist_ai_verdicts_to_entities

        ext_id = sample_ner_run["entity_id"]
        run_id = sample_ner_run["run_id"]
        ext = (
            await db_session.execute(
                select(ExtractionApproval).where(ExtractionApproval.id == ext_id)
            )
        ).scalar_one()

        verdicts = [
            {
                "candidate": {"_entity_id": str(ext_id)},
                "verdict": {
                    "overall":       "full",
                    "name_ok":       "yes",
                    "type_ok":       "yes",
                    "role_ok":       "yes",
                    "reasoning":     "Perfect match.",
                    "suggested_fix": None,
                },
                "judge_id":     "gemini-3.1-pro-preview",
                "judged_at":    "2026-06-07T00:00:00Z",
                "cache_key":    "c" * 64,
                "evaluator_id": "person_ner",
            },
        ]

        await _persist_ai_verdicts_to_entities(
            run_id=str(run_id),
            session_id="20260607T000001Z",
            verdicts=verdicts,
            entities=[ext],
        )

        ext = (
            await db_session.execute(
                select(ExtractionApproval).where(ExtractionApproval.id == ext_id)
            )
        ).scalar_one()
        await db_session.refresh(ext)

        assert ext.ai_verdict is not None
        # Key must exist with value None/null (not missing).
        assert "suggested_fix" in ext.ai_verdict
        assert ext.ai_verdict["suggested_fix"] is None


# ── 3-4. Cache round-trip preserves suggested_fix ─────────────────────────

class TestCacheRoundTripWithFix:
    @pytest.mark.asyncio
    async def test_write_and_read_back_preserves_fix(
        self, db_session, sample_ner_run,
    ) -> None:
        from app.models.extraction_approval import ExtractionApproval
        from app.routers.extraction_verify import _write_ner_verdicts_to_cache
        from app.pipeline.ner_verdict_cache import ner_verdict_query_summary
        from app.pipeline.inference_cache import read_from_inference_cache

        ext_id = sample_ner_run["entity_id"]
        ext = (
            await db_session.execute(
                select(ExtractionApproval).where(ExtractionApproval.id == ext_id)
            )
        ).scalar_one()

        fix_payload = {
            "text":         "יוסף בן יעקב",
            "reasoning":    "Full name in colophon_text.",
            "source_field": "colophon_text",
            "confidence":   "high",
        }
        verdicts = [
            {
                "candidate": {"_entity_id": str(ext_id)},
                "verdict": {
                    "overall":       "partial",
                    "name_ok":       "partial",
                    "type_ok":       "yes",
                    "role_ok":       "yes",
                    "reasoning":     "Truncated.",
                    "suggested_fix": fix_payload,
                },
                "judge_id":     "gemini-3.1-pro-preview",
                "judged_at":    "2026-06-07T00:00:00Z",
                "cache_key":    "d" * 64,
                "evaluator_id": "person_ner",
            },
        ]

        await _write_ner_verdicts_to_cache(
            entities=[ext],
            verdicts=verdicts,
        )

        qs = ner_verdict_query_summary(ext, "gemini-3.1-pro-preview")
        hit = await read_from_inference_cache(
            db_session, kind="ai_verdict", query_summary=qs,
        )
        assert hit is not None
        cached_fix = hit.get("verdict", {}).get("suggested_fix")
        assert cached_fix is not None
        assert cached_fix["text"] == "יוסף בן יעקב"

    @pytest.mark.asyncio
    async def test_cache_preserves_null_fix(
        self, db_session, sample_ner_run,
    ) -> None:
        """null suggested_fix must survive the cache write/read cycle."""
        from app.models.extraction_approval import ExtractionApproval
        from app.routers.extraction_verify import _write_ner_verdicts_to_cache
        from app.pipeline.ner_verdict_cache import ner_verdict_query_summary
        from app.pipeline.inference_cache import read_from_inference_cache

        ext_id = sample_ner_run["entity_id"]
        ext = (
            await db_session.execute(
                select(ExtractionApproval).where(ExtractionApproval.id == ext_id)
            )
        ).scalar_one()

        verdicts = [
            {
                "candidate": {"_entity_id": str(ext_id)},
                "verdict": {
                    "overall":       "full",
                    "name_ok":       "yes",
                    "type_ok":       "yes",
                    "role_ok":       "yes",
                    "reasoning":     "Perfect match.",
                    "suggested_fix": None,
                },
                "judge_id":     "gemini-3.1-pro-preview",
                "judged_at":    "2026-06-07T00:00:00Z",
                "cache_key":    "e" * 64,
                "evaluator_id": "person_ner",
            },
        ]

        await _write_ner_verdicts_to_cache(
            entities=[ext],
            verdicts=verdicts,
        )

        qs = ner_verdict_query_summary(ext, "gemini-3.1-pro-preview")
        hit = await read_from_inference_cache(
            db_session, kind="ai_verdict", query_summary=qs,
        )
        assert hit is not None
        verdict_dict = hit.get("verdict", {})
        assert "suggested_fix" in verdict_dict
        assert verdict_dict["suggested_fix"] is None


# ── 5. _approval_to_ner_shape propagates exists_in grounding hint ─────────

class TestApprovalToNerShapeGrounding:
    def test_grounded_exists_in_sets_grounded_true(self):
        """An entity with exists_in='grounded' should produce grounded=True."""
        from app.routers.extraction_verify import _approval_to_ner_shape
        from app.models.extraction_approval import ExtractionApproval

        ext = ExtractionApproval(
            id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            control_number="001",
            source="person_ner",
            text="יוסף",
            start=0,
            end=5,
            type="PERSON",
            role="TRANSCRIBER",
            confidence=0.82,
            model_confidence=0.91,
            approved=False,
            exists_in="grounded",
        )
        shape = _approval_to_ner_shape(ext)
        assert shape["grounded"] is True

    def test_wrong_field_exists_in_sets_grounded_false(self):
        from app.routers.extraction_verify import _approval_to_ner_shape
        from app.models.extraction_approval import ExtractionApproval

        ext = ExtractionApproval(
            id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            control_number="001",
            source="provenance_ner",
            text="שמעון",
            start=0,
            end=5,
            type="OWNER",
            confidence=0.72,
            approved=False,
            exists_in="wrong_field",
        )
        shape = _approval_to_ner_shape(ext)
        assert shape["grounded"] is False

    def test_no_exists_in_sets_grounded_none(self):
        from app.routers.extraction_verify import _approval_to_ner_shape
        from app.models.extraction_approval import ExtractionApproval

        ext = ExtractionApproval(
            id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            control_number="001",
            source="contents_ner",
            text="תהלים",
            start=0,
            end=6,
            type="WORK",
            confidence=0.80,
            approved=False,
            exists_in=None,
        )
        shape = _approval_to_ner_shape(ext)
        assert shape.get("grounded") is None

    def test_override_text_is_the_judged_text(self):
        """After an Auto-fix, the eval-agent must judge the corrected
        (override) text, not the stale original — otherwise the re-check
        is meaningless."""
        from app.routers.extraction_verify import _approval_to_ner_shape
        from app.models.extraction_approval import ExtractionApproval

        ext = ExtractionApproval(
            id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            control_number="001",
            source="person_ner",
            text="יוסף",
            override_text="יוסף בן יעקב",
            start=0,
            end=4,
            type="PERSON",
            role="TRANSCRIBER",
            confidence=0.82,
            approved=False,
            exists_in="grounded",
        )
        shape = _approval_to_ner_shape(ext)
        assert shape["text"] == "יוסף בן יעקב"
        # The snapshotted exists_in describes the ORIGINAL span, so once the
        # text is overridden the grounding hint is dropped (judge searches fresh).
        assert "grounded" not in shape

    def test_override_equal_to_text_keeps_grounding(self):
        """A no-op override (== original) is not a real edit, so the
        grounding hint is preserved."""
        from app.routers.extraction_verify import _approval_to_ner_shape
        from app.models.extraction_approval import ExtractionApproval

        ext = ExtractionApproval(
            id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            control_number="001",
            source="person_ner",
            text="יוסף",
            override_text="יוסף",
            start=0,
            end=4,
            type="PERSON",
            role="AUTHOR",
            confidence=0.9,
            approved=False,
            exists_in="grounded",
        )
        shape = _approval_to_ner_shape(ext)
        assert shape["text"] == "יוסף"
        assert shape["grounded"] is True
