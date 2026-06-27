"""Integration tests for Wikidata Studio eval-agent verification endpoints."""

from __future__ import annotations

import uuid

import pytest


class TestWikidataVerifyActions:
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, async_client) -> None:
        fake_run = uuid.uuid4()
        r = await async_client.get(
            f"/api/runs/{fake_run}/wikidata-studio/ai-verify/actions",
        )
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_single_scope_returns_wikidata_item_action(self, sample_run) -> None:
        client = sample_run["client"]
        run_id = sample_run["run_id"]

        r = await client.get(
            f"/api/runs/{run_id}/wikidata-studio/ai-verify/actions"
            "?scope_kind=single",
        )

        assert r.status_code == 200
        actions = r.json()
        ids = {a["id"] for a in actions}
        assert "audit_wikidata_item" in ids
        assert "autofix_from_wikidata" in ids
        action = next(a for a in actions if a["id"] == "audit_wikidata_item")
        assert action["evaluators"] == ["wikidata_item"]
        autofix = next(a for a in actions if a["id"] == "autofix_from_wikidata")
        assert autofix["evaluators"] == ["wikidata_autofix"]
        assert set(action) == {
            "id", "label", "description", "scope_kinds",
            "evaluators", "min_candidates",
        }

    @pytest.mark.asyncio
    async def test_invalid_scope_kind_rejected(self, sample_run) -> None:
        client = sample_run["client"]
        run_id = sample_run["run_id"]

        r = await client.get(
            f"/api/runs/{run_id}/wikidata-studio/ai-verify/actions"
            "?scope_kind=garbage",
        )

        assert r.status_code == 422


class TestWikidataVerifySessions:
    @pytest.mark.asyncio
    async def test_empty_sessions_returns_list(self, sample_run) -> None:
        client = sample_run["client"]
        run_id = sample_run["run_id"]

        r = await client.get(
            f"/api/runs/{run_id}/wikidata-studio/ai-verify/sessions",
        )

        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestWikidataVerifyStartStream:
    @pytest.mark.asyncio
    async def test_unknown_action_rejected(self, sample_run) -> None:
        client = sample_run["client"]
        run_id = sample_run["run_id"]

        r = await client.post(
            f"/api/runs/{run_id}/wikidata-studio/ai-verify/start-stream",
            json={"action_id": "not_a_real_action", "item_ids": ["person::x"]},
        )

        assert r.status_code == 400
        assert "unknown action_id" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_no_items_in_scope_rejected(self, sample_run) -> None:
        client = sample_run["client"]
        run_id = sample_run["run_id"]

        r = await client.post(
            f"/api/runs/{run_id}/wikidata-studio/ai-verify/start-stream",
            json={
                "action_id": "audit_wikidata_item",
                "item_ids": ["missing::local-id"],
            },
        )

        assert r.status_code == 400
        assert r.json()["detail"] == "no Wikidata Studio items in scope"

    @pytest.mark.asyncio
    async def test_cached_item_stream_does_not_require_eval_agent(
        self, sample_run, monkeypatch,
    ) -> None:
        from app.routers import wikidata_studio as ws_router

        client = sample_run["client"]
        run_id = sample_run["run_id"]
        item = {
            "_local_id": "person::Moses Maimonides",
            "local_id": "person::Moses Maimonides",
            "entity_type": "person",
            "labels": {"en": "Moses Maimonides"},
            "statements": [],
            "record_ids": [sample_run["control_number"]],
        }
        cached = {
            "verdict": {
                "overall": "pass",
                "name_ok": "yes",
                "type_ok": "yes",
                "role_ok": "n/a",
                "reasoning": "cached verdict",
            },
            "judge_id": "gemini-test",
            "judged_at": "2026-06-07T00:00:00Z",
            "cache_key": "cache-key",
            "evaluator": "wikidata_item",
            "confidence": 1.0,
            "sub_type": "person",
            "record_id": sample_run["control_number"],
        }

        async def _items(*_args, **_kwargs):
            return [item], [{"_control_number": sample_run["control_number"]}]

        async def _key(*_args, **_kwargs):
            return "test-key"

        async def _cache(*_args, **_kwargs):
            return cached

        def _missing_eval_agent():
            raise FileNotFoundError("eval-agent not present")

        monkeypatch.setattr(ws_router, "_fetch_wikidata_verify_items", _items)
        monkeypatch.setattr(ws_router, "_resolve_gemini_key", _key)
        monkeypatch.setattr(ws_router, "read_from_inference_cache", _cache)
        monkeypatch.setattr(ws_router, "locate_eval_agent", _missing_eval_agent)

        async with client.stream(
            "POST",
            f"/api/runs/{run_id}/wikidata-studio/ai-verify/start-stream",
            json={
                "action_id": "audit_wikidata_item",
                "item_ids": ["person::Moses Maimonides"],
            },
        ) as r:
            body = await r.aread()

        text = body.decode("utf-8")
        assert r.status_code == 200
        assert "event: agent.verdict" in text
        assert "cached verdict" in text
        assert "runner.warning" not in text

    @pytest.mark.asyncio
    async def test_uncached_missing_eval_agent_emits_runner_warning(
        self, sample_run, monkeypatch,
    ) -> None:
        from app.routers import wikidata_studio as ws_router

        client = sample_run["client"]
        run_id = sample_run["run_id"]
        item = {
            "_local_id": "manuscript::990001801390205171",
            "local_id": "manuscript::990001801390205171",
            "entity_type": "manuscript",
            "labels": {"en": "Jerusalem, NLI, 990001801390205171"},
            "statements": [],
            "record_ids": [sample_run["control_number"]],
        }

        async def _items(*_args, **_kwargs):
            return [item], [{"_control_number": sample_run["control_number"]}]

        async def _key(*_args, **_kwargs):
            return "test-key"

        async def _no_cache(*_args, **_kwargs):
            return None

        def _missing_eval_agent():
            raise FileNotFoundError("eval-agent not present")

        monkeypatch.setattr(ws_router, "_fetch_wikidata_verify_items", _items)
        monkeypatch.setattr(ws_router, "_resolve_gemini_key", _key)
        monkeypatch.setattr(ws_router, "read_from_inference_cache", _no_cache)
        monkeypatch.setattr(ws_router, "locate_eval_agent", _missing_eval_agent)

        async with client.stream(
            "POST",
            f"/api/runs/{run_id}/wikidata-studio/ai-verify/start-stream",
            json={
                "action_id": "audit_wikidata_item",
                "item_ids": ["manuscript::990001801390205171"],
            },
        ) as r:
            body = await r.aread()

        text = body.decode("utf-8")
        assert r.status_code == 200
        assert "event: runner.warning" in text
        assert "eval-agent is not available" in text
        assert "event: session.end" in text
        assert "uncached_skipped" in text
