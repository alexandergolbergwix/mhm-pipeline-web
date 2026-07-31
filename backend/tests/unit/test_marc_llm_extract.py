"""Rule W-140 — LLM proposals must be span-grounded, closed-vocabulary, advisory."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

from app.pipeline.marc_llm_extract import (
    STATUS_NO_SOURCE,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    attach_llm_proposals,
    build_prompt,
    extract_for_record,
    parse_response,
    source_text,
    validate_proposal,
)
from app.pipeline.wikidata_verify_evidence import build_verify_evidence_pack

RECORD = (
    "[provenance] 561$a: בדף 241א רשימת הבעלים \"אברהם היכיני\" "
    "המזכירה את נשואי הוריו\n"
    "[notes] 500$a: נכתב בוונטו, צפון איטליה. כתב יד על קלף."
)


def _marc() -> dict[str, object]:
    return {
        "provenance": '561$a: בדף 241א רשימת הבעלים "אברהם היכיני" המזכירה את נשואי הוריו',
        "notes": ["500$a: נכתב בוונטו, צפון איטליה. כתב יד על קלף."],
        "shelfmark": "Ms. Heb. 1",
    }


class TestSourceText:
    def test_provenance_and_notes_are_tagged_by_slice(self) -> None:
        text = source_text(_marc())
        assert "[provenance]" in text
        assert "[notes]" in text
        assert "אברהם היכיני" in text

    def test_record_without_prose_has_no_source(self) -> None:
        assert source_text({"shelfmark": "Ms. Heb. 1"}) == ""
        assert source_text(None) == ""


class TestValidateProposal:
    def test_grounded_proposal_survives(self) -> None:
        proposal = validate_proposal(
            {
                "property_id": "P127",
                "value": "אברהם היכיני",
                "span": "רשימת הבעלים",
                "marc_tag": "561$a",
                "confidence": "high",
            },
            RECORD,
        )
        assert proposal is not None
        assert proposal["channel"] == "llm_marc_extraction"
        assert proposal["confidence"] == "high"

    def test_hallucinated_span_is_dropped(self) -> None:
        """The whole safety property: a span not in the record cannot pass."""
        assert validate_proposal(
            {
                "property_id": "P127",
                "value": "Moses Gaster",
                "span": "owned by Moses Gaster in 1897",
                "marc_tag": "561$a",
            },
            RECORD,
        ) is None

    def test_unsupported_property_is_dropped(self) -> None:
        assert validate_proposal(
            {"property_id": "P585", "value": "1460", "span": "נכתב בוונטו"},
            RECORD,
        ) is None

    def test_material_must_resolve_to_the_closed_vocabulary(self) -> None:
        ok = validate_proposal(
            {"property_id": "P186", "value": "קלף", "span": "כתב יד על קלף"},
            RECORD,
        )
        assert ok is not None
        assert ok["value"] == "Q226697"

        assert validate_proposal(
            {"property_id": "P186", "value": "brown ink", "span": "כתב יד על קלף"},
            RECORD,
        ) is None

    def test_value_or_span_missing_is_dropped(self) -> None:
        assert validate_proposal({"property_id": "P127", "value": "x"}, RECORD) is None
        assert validate_proposal({"property_id": "P127", "span": "נכתב"}, RECORD) is None


class TestParseResponse:
    def test_fenced_json_is_tolerated_and_deduped(self) -> None:
        body = "```json\n" + json.dumps({
            "proposals": [
                {"property_id": "P127", "value": "אברהם היכיני", "span": "רשימת הבעלים"},
                {"property_id": "P127", "value": "אברהם היכיני", "span": "רשימת הבעלים"},
                {"property_id": "P186", "value": "קלף", "span": "על קלף"},
            ],
        }) + "\n```"
        proposals = parse_response(body, RECORD)
        assert [p["property_id"] for p in proposals] == ["P127", "P186"]

    def test_unparseable_response_yields_nothing(self) -> None:
        assert parse_response("I could not find anything.", RECORD) == []

    def test_missing_proposals_key_yields_nothing(self) -> None:
        assert parse_response(json.dumps({"result": []}), RECORD) == []


class TestExtractForRecord:
    def test_prose_is_extracted(self) -> None:
        payload = json.dumps({
            "proposals": [
                {
                    "property_id": "P1071",
                    "value": "ונטו",
                    "span": "נכתב בוונטו",
                    "marc_tag": "500$a",
                    "confidence": "medium",
                },
            ],
        })
        result = asyncio.run(
            extract_for_record(
                None, control_number="990001", marc_slice=_marc(), call=lambda: payload,
            ),
        )
        assert result["status"] == STATUS_OK
        assert result["proposals"][0]["value"] == "ונטו"

    def test_record_without_prose_is_not_sent_to_the_model(self) -> None:
        calls = []

        def call() -> str:
            calls.append(1)
            return "{}"

        result = asyncio.run(
            extract_for_record(
                None, control_number="x", marc_slice={"shelfmark": "A"}, call=call,
            ),
        )
        assert result["status"] == STATUS_NO_SOURCE
        assert not calls

    def test_model_failure_is_unavailable_not_empty(self) -> None:
        def boom() -> str:
            raise RuntimeError("502 upstream")

        result = asyncio.run(
            extract_for_record(
                None, control_number="x", marc_slice=_marc(), call=boom,
            ),
        )
        assert result["status"] == STATUS_UNAVAILABLE
        assert result["proposals"] == []


class TestAttachAndSurface:
    def test_proposals_reach_the_evidence_pack_but_not_the_statements(self) -> None:
        item = {
            "local_id": "QDraft_MS_1",
            "entity_type": "manuscript",
            "statements": [{"property_id": "P31", "value": "Q87167"}],
            "verify_evidence": {"marc": _marc()},
        }
        payload = json.dumps({
            "proposals": [
                {"property_id": "P186", "value": "קלף", "span": "על קלף", "marc_tag": "500$a"},
            ],
        })
        stats = asyncio.run(attach_llm_proposals(None, [item], call=lambda: payload))

        assert stats["records"] == 1
        assert stats["proposals"] == 1
        assert stats["unavailable"] == 0
        pack = build_verify_evidence_pack(item, [])
        surfaced = pack["llm_proposals"]["proposals"]
        assert surfaced[0]["value"] == "Q226697"
        # Advisory only — nothing was projected into the item's claims.
        assert [s["property_id"] for s in item["statements"]] == ["P31"]

    def test_budget_exhaustion_is_reported_not_silent(self) -> None:
        items = [
            {
                "local_id": f"ms{i}",
                "entity_type": "manuscript",
                "verify_evidence": {"marc": _marc()},
            }
            for i in range(3)
        ]
        stats = asyncio.run(
            attach_llm_proposals(
                None, items, call=lambda: json.dumps({"proposals": []}), budget=1,
            ),
        )
        assert stats["records"] == 1
        assert stats["skipped"] == 2
        assert "budget" in items[2]["_llm_proposals"]["note"]

    def test_non_manuscript_items_are_untouched(self) -> None:
        item = {"local_id": "w1", "entity_type": "work", "verify_evidence": {"marc": _marc()}}
        stats = asyncio.run(attach_llm_proposals(None, [item], call=lambda: "{}"))
        assert stats["records"] == 0
        assert "_llm_proposals" not in item

    def test_unprobed_item_reports_not_run(self) -> None:
        pack = build_verify_evidence_pack(
            {"local_id": "m", "entity_type": "manuscript", "statements": []}, [],
        )
        assert pack["llm_proposals"]["status"] == "not_run"


def test_prompt_names_the_closed_material_vocabulary() -> None:
    prompt = build_prompt("990001", RECORD)
    assert "קלף" in prompt
    assert "VERBATIM" in prompt
    assert "P127" in prompt


class TestQubridCall:
    """The live seam: request shape against the tier-1 registry entry."""

    def test_request_targets_the_registry_model_and_base_url(self, monkeypatch) -> None:
        import httpx

        from app.pipeline import marc_llm_extract

        captured: dict[str, object] = {}

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"choices": [{"message": {"content": '{"proposals": []}'}}]}

        def fake_post(url, *, json, headers, timeout):  # noqa: A002
            captured.update(url=url, json=json, headers=headers, timeout=timeout)
            return FakeResponse()

        monkeypatch.setattr(httpx, "post", fake_post)
        monkeypatch.setenv("QUBRID_API_KEY", "test-key")

        body = marc_llm_extract._call_qubrid(
            "prompt", model_id="deepseek-ai/DeepSeek-V4-Flash", timeout=5,
        )

        assert body == '{"proposals": []}'
        assert captured["url"] == "https://platform.qubrid.com/v1/chat/completions"
        assert captured["json"]["model"] == "deepseek-ai/DeepSeek-V4-Flash"
        assert captured["json"]["temperature"] == 0
        assert captured["headers"]["Authorization"] == "Bearer test-key"

    def test_missing_credentials_raise_rather_than_return_empty(self, monkeypatch) -> None:
        from app.pipeline.judge_models import Tier1CredentialsError
        from app.pipeline import marc_llm_extract

        monkeypatch.delenv("QUBRID_API_KEY", raising=False)
        try:
            marc_llm_extract._call_qubrid(
                "p", model_id="deepseek-ai/DeepSeek-V4-Flash", timeout=5,
            )
        except Tier1CredentialsError:
            return
        raise AssertionError("missing credentials must raise")


class TestSessionLifetime:
    """Rule W-40 — no DB session may stay open across the model call.

    Holding the caller's session across 37 multi-second calls closed the
    connection and the next cache read raised
    `InterfaceError: connection is closed`.
    """

    def test_no_session_is_open_while_the_model_is_called(self) -> None:
        import contextlib

        open_sessions = {"count": 0, "max_during_call": 0}
        state = {"in_call": False}

        class FakeSession:
            pass

        @contextlib.asynccontextmanager
        async def factory():
            open_sessions["count"] += 1
            if state["in_call"]:
                open_sessions["max_during_call"] = max(
                    open_sessions["max_during_call"], open_sessions["count"],
                )
            try:
                yield FakeSession()
            finally:
                open_sessions["count"] -= 1

        def call() -> str:
            state["in_call"] = True
            # A session opened here would be held across the network call.
            open_sessions["max_during_call"] = max(
                open_sessions["max_during_call"], open_sessions["count"],
            )
            state["in_call"] = False
            return json.dumps({"proposals": []})

        async def run() -> None:
            with patch(
                "app.pipeline.marc_llm_extract.read_from_inference_cache",
                new=AsyncMock(return_value=None),
            ), patch(
                "app.pipeline.marc_llm_extract.write_to_inference_cache",
                new=AsyncMock(),
            ):
                await extract_for_record(
                    factory,
                    control_number="990001",
                    marc_slice=_marc(),
                    call=call,
                )

        asyncio.run(run())
        assert open_sessions["max_during_call"] == 0, (
            "a DB session was open during the model call"
        )
        assert open_sessions["count"] == 0, "a session leaked"

    def test_cache_hit_skips_the_model_entirely(self) -> None:
        import contextlib

        calls: list[int] = []

        @contextlib.asynccontextmanager
        async def factory():
            yield object()

        cached = {"status": STATUS_OK, "proposals": [], "model": "m"}

        async def run() -> dict:
            with patch(
                "app.pipeline.marc_llm_extract.read_from_inference_cache",
                new=AsyncMock(return_value=cached),
            ):
                return await extract_for_record(
                    factory,
                    control_number="990001",
                    marc_slice=_marc(),
                    call=lambda: calls.append(1) or "{}",
                )

        result = asyncio.run(run())
        assert result is cached
        assert not calls, "cache hit still called the model"

    def test_a_failed_call_is_not_cached(self) -> None:
        import contextlib

        @contextlib.asynccontextmanager
        async def factory():
            yield object()

        def boom() -> str:
            raise RuntimeError("502 upstream")

        write = AsyncMock()

        async def run() -> dict:
            with patch(
                "app.pipeline.marc_llm_extract.read_from_inference_cache",
                new=AsyncMock(return_value=None),
            ), patch(
                "app.pipeline.marc_llm_extract.write_to_inference_cache", new=write,
            ):
                return await extract_for_record(
                    factory, control_number="x", marc_slice=_marc(), call=boom,
                )

        result = asyncio.run(run())
        assert result["status"] == STATUS_UNAVAILABLE
        write.assert_not_awaited()


class TestScaling:
    """Rule W-140 — the corpus path must be O(1) round trips and concurrent."""

    @staticmethod
    def _factory(calls: list[str]):
        import contextlib

        @contextlib.asynccontextmanager
        async def factory():
            calls.append("session")
            yield object()

        return factory

    def _items(self, n: int) -> list[dict]:
        return [
            {
                "local_id": f"ms{i}",
                "entity_type": "manuscript",
                "_primary_control_number": f"99000{i}",
                "verify_evidence": {"marc": _marc()},
            }
            for i in range(n)
        ]

    def test_cache_is_read_once_for_the_whole_corpus(self) -> None:
        sessions: list[str] = []
        reads: list[int] = []

        async def read_many(_db, *, kind, query_summaries):
            reads.append(len(query_summaries))
            return {}

        async def write_many(_db, **_kwargs):
            return None

        async def run() -> None:
            with patch(
                "app.pipeline.inference_cache.read_many_from_inference_cache",
                new=read_many,
            ), patch(
                "app.pipeline.inference_cache.write_many_to_inference_cache",
                new=write_many,
            ):
                await attach_llm_proposals(
                    self._factory(sessions),
                    self._items(50),
                    call=lambda: json.dumps({"proposals": []}),
                )

        asyncio.run(run())
        assert reads == [50], f"expected one bulk read of 50 keys, got {reads}"
        assert len(sessions) == 2, f"expected 2 short sessions, got {len(sessions)}"

    def test_cached_records_never_reach_the_model(self) -> None:
        from app.pipeline.inference_cache import canonical_hash

        sessions: list[str] = []
        model_calls: list[int] = []

        async def read_many(_db, *, kind, query_summaries):
            return {
                canonical_hash(s): {"status": STATUS_OK, "proposals": []}
                for s in query_summaries
            }

        async def run() -> dict:
            with patch(
                "app.pipeline.inference_cache.read_many_from_inference_cache",
                new=read_many,
            ):
                return await attach_llm_proposals(
                    self._factory(sessions),
                    self._items(20),
                    call=lambda: model_calls.append(1) or "{}",
                )

        stats = asyncio.run(run())
        assert stats["cached"] == 20
        assert not model_calls, "a fully cached corpus still called the model"

    def test_calls_overlap_rather_than_running_serially(self, monkeypatch) -> None:
        monkeypatch.setenv("MARC_LLM_EXTRACT_CONCURRENCY", "5")
        inflight = {"now": 0, "max": 0}

        def call() -> str:
            inflight["now"] += 1
            inflight["max"] = max(inflight["max"], inflight["now"])
            time.sleep(0.02)
            inflight["now"] -= 1
            return json.dumps({"proposals": []})

        asyncio.run(attach_llm_proposals(None, self._items(10), call=call))
        assert inflight["max"] > 1, "extraction ran serially"

    def test_budget_still_caps_the_model_calls(self) -> None:
        items = self._items(5)
        stats = asyncio.run(
            attach_llm_proposals(
                None, items, call=lambda: json.dumps({"proposals": []}), budget=2,
            ),
        )
        assert stats["records"] == 2
        assert stats["skipped"] == 3
