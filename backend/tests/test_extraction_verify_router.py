"""Tests for the ``/runs/{id}/extraction/ai-verify/*`` endpoint family.

Pins five contracts:

1. ``GET /actions?scope_kind=all`` returns ``audit_ner_extraction`` with
   its four evaluators visible.
2. ``POST /start-stream`` with no Gemini key configured returns 400
   with a friendly message.
3. ``_persist_ai_verdicts_to_entities`` writes the verdict summary
   into ``ExtractionApproval.ai_verdict`` keyed by ``_entity_id``.
4. ``_ner_verdict_query_summary`` is stable and content-based.
5. **Regression — locate_eval_agent / sse_stream silent failure**:
   - When all entities are warm in the inference cache, ``start-stream``
     returns 200 + SSE verdicts even when ``locate_eval_agent()`` would
     raise ``FileNotFoundError`` (i.e. Heroku with no sibling repo).
   - When entities are NOT cached and ``locate_eval_agent()`` fails,
     ``start-stream`` still returns 200 (streaming started) and emits a
     ``runner.error`` SSE event — not a silent stream-end that leaves
     the button stuck.
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


class TestNerVerdictQuerySummary:
    @pytest.mark.asyncio
    async def test_query_summary_is_stable_and_content_based(
        self, db_session, sample_extraction_run,
    ) -> None:
        """_ner_verdict_query_summary must hash only entity content fields,
        so the same entity text+type+role always produces the same key."""
        from app.models.extraction_approval import ExtractionApproval
        from app.routers.extraction_verify import _ner_verdict_query_summary
        from app.pipeline.inference_cache import canonical_hash
        from sqlalchemy import select

        ext_id = sample_extraction_run["entity_id"]
        ext = (
            await db_session.execute(
                select(ExtractionApproval).where(ExtractionApproval.id == ext_id)
            )
        ).scalar_one()

        qs1 = _ner_verdict_query_summary(ext)
        qs2 = _ner_verdict_query_summary(ext)

        assert canonical_hash(qs1) == canonical_hash(qs2)
        assert "text" in qs1
        assert qs1["text"] == "Maimonides"

    @pytest.mark.asyncio
    async def test_different_roles_produce_different_cache_keys(
        self, db_session, sample_extraction_run,
    ) -> None:
        """Changing the role field must change the cache key."""
        import copy
        from app.models.extraction_approval import ExtractionApproval
        from app.routers.extraction_verify import _ner_verdict_query_summary
        from app.pipeline.inference_cache import canonical_hash
        from sqlalchemy import select

        ext_id = sample_extraction_run["entity_id"]
        ext = (
            await db_session.execute(
                select(ExtractionApproval).where(ExtractionApproval.id == ext_id)
            )
        ).scalar_one()

        qs_orig = _ner_verdict_query_summary(ext)

        ext_copy = copy.copy(ext)
        ext_copy.role = "scribe"
        qs_scribe = _ner_verdict_query_summary(ext_copy)

        assert canonical_hash(qs_orig) != canonical_hash(qs_scribe)


class TestInferenceCachePreCheck:
    @pytest.mark.asyncio
    async def test_cached_verdict_is_written_and_read_back(
        self, db_session, sample_extraction_run,
    ) -> None:
        """write_to_inference_cache + read_from_inference_cache round-trips
        through the Postgres L2 tier (Redis absent in SQLite CI)."""
        from app.models.extraction_approval import ExtractionApproval
        from app.routers.extraction_verify import _ner_verdict_query_summary
        from app.pipeline.inference_cache import (
            read_from_inference_cache,
            write_to_inference_cache,
        )
        from sqlalchemy import select

        ext_id = sample_extraction_run["entity_id"]
        ext = (
            await db_session.execute(
                select(ExtractionApproval).where(ExtractionApproval.id == ext_id)
            )
        ).scalar_one()

        qs = _ner_verdict_query_summary(ext)
        verdict_payload = {
            "overall": "pass",
            "name_ok": True,
            "type_ok": True,
            "role_ok": True,
            "reasoning": "Maimonides is a known author.",
            "model": "gemini-test",
            "judged_at": "2026-06-03T00:00:00Z",
            "session_id": "20260603T000000Z",
            "evaluator": "person_ner",
        }

        await write_to_inference_cache(
            db_session, kind="ai_verdict", query_summary=qs, result=verdict_payload,
        )

        hit = await read_from_inference_cache(
            db_session, kind="ai_verdict", query_summary=qs,
        )
        assert hit is not None
        assert hit["overall"] == "pass"
        assert hit["evaluator"] == "person_ner"

    @pytest.mark.asyncio
    async def test_cold_cache_returns_none(
        self, db_session, sample_extraction_run,
    ) -> None:
        """Before any verdict is written, read must return None."""
        from app.models.extraction_approval import ExtractionApproval
        from app.routers.extraction_verify import _ner_verdict_query_summary
        from app.pipeline.inference_cache import read_from_inference_cache
        from sqlalchemy import select

        ext_id = sample_extraction_run["entity_id"]
        ext = (
            await db_session.execute(
                select(ExtractionApproval).where(ExtractionApproval.id == ext_id)
            )
        ).scalar_one()

        qs = _ner_verdict_query_summary(ext)
        hit = await read_from_inference_cache(
            db_session, kind="ai_verdict", query_summary=qs,
        )
        assert hit is None


# ── SSE frame parser (shared by regression tests below) ───────────────────

import json as _json  # noqa: E402


def _parse_sse_frames(text: str) -> list[dict]:
    events = []
    for frame in text.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        ev_type = "message"
        data = ""
        for line in frame.split("\n"):
            if line.startswith(": "):
                continue
            colon = line.find(":")
            if colon < 0:
                continue
            field = line[:colon]
            value = line[colon + 1:].lstrip(" ")
            if field == "event":
                ev_type = value
            elif field == "data":
                data = value
        if not data:
            continue
        try:
            payload = _json.loads(data)
        except _json.JSONDecodeError:
            payload = {"raw": data}
        events.append({"type": ev_type, **payload})
    return events


class TestStartStreamSilentFailureRegression:
    """Regression suite for the 'button silently reverts after 0.5s' bug.

    Two failure modes existed:

    A. locate_eval_agent() was called unconditionally before the cache
       pre-check, so even a fully-cached run would silently fail when
       the eval-agent sibling repo was absent (e.g. Heroku).

    B. sse_stream's producer() swallowed generator exceptions, so the
       client received a clean done:true with zero events and no error
       message — the button reverted silently.
    """

    @pytest.mark.asyncio
    async def test_fully_cached_run_returns_verdicts_without_eval_agent(
        self, db_session, sample_extraction_run, monkeypatch,
    ) -> None:
        """Regression A: all entities cached → locate_eval_agent never called.

        Even if ``locate_eval_agent()`` would raise, the endpoint must
        return 200 and stream all cached verdicts to the client.
        """
        from app.models.extraction_approval import ExtractionApproval
        from app.pipeline.inference_cache import write_to_inference_cache
        from app.routers.extraction_verify import _ner_verdict_query_summary
        from sqlalchemy import select
        import app.routers.extraction_verify as _ev

        monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")

        # Patch locate_eval_agent to simulate a Heroku dyno with no sibling.
        def _no_eval_agent():
            raise FileNotFoundError("eval-agent not found (test patch)")
        monkeypatch.setattr(_ev, "locate_eval_agent", _no_eval_agent)

        ext_id = sample_extraction_run["entity_id"]
        ext = (
            await db_session.execute(
                select(ExtractionApproval).where(ExtractionApproval.id == ext_id)
            )
        ).scalar_one()
        qs = _ner_verdict_query_summary(ext)
        await write_to_inference_cache(
            db_session,
            kind="ai_verdict",
            query_summary=qs,
            result={
                "verdict": {"overall": "pass", "name_ok": True},
                "judge_id": "gemini-test",
                "judged_at": "2026-06-03T00:00:00Z",
                "evaluator": "person_ner",
            },
        )

        client = sample_extraction_run["client"]
        run_id = sample_extraction_run["run_id"]
        r = await client.post(
            f"/api/runs/{run_id}/extraction/ai-verify/start-stream",
            json={"action_id": "audit_ner_extraction"},
        )

        assert r.status_code == 200, f"unexpected status: {r.status_code} {r.text}"
        events = _parse_sse_frames(r.text)
        by_type = {e["type"]: e for e in events}

        assert "session.start" in by_type, "session.start must be emitted"
        assert "agent.verdict" in by_type, "cached verdict must be emitted"
        assert "session.end"   in by_type, "session.end must be emitted"
        assert "runner.error"  not in by_type, "no error when all entities cached"
        assert by_type["session.start"].get("cache_hits") == 1

    @pytest.mark.asyncio
    async def test_uncached_run_with_missing_eval_agent_emits_runner_error(
        self, db_session, sample_extraction_run, monkeypatch,
    ) -> None:
        """Regression B: uncached entity + missing eval-agent → runner.error via SSE.

        The response must be HTTP 200 (streaming started) and contain a
        ``runner.error`` event — not a pre-stream 500 that never reaches the
        client's error handler, and not a silent stream-end that leaves the
        button stuck.
        """
        import app.routers.extraction_verify as _ev

        monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")

        def _no_eval_agent():
            raise FileNotFoundError("eval-agent not found (test patch)")
        monkeypatch.setattr(_ev, "locate_eval_agent", _no_eval_agent)

        # No verdict written to cache → entity is uncached → subprocess needed.

        client = sample_extraction_run["client"]
        run_id = sample_extraction_run["run_id"]
        r = await client.post(
            f"/api/runs/{run_id}/extraction/ai-verify/start-stream",
            json={"action_id": "audit_ner_extraction"},
        )

        assert r.status_code == 200, (
            "should be 200 (streaming); a 500 here means locate_eval_agent "
            "is still called outside the generator (regression)"
        )
        events = _parse_sse_frames(r.text)
        assert any(e["type"] == "runner.error" for e in events), (
            "missing eval-agent must produce runner.error SSE event, "
            "not a silent stream-end"
        )
