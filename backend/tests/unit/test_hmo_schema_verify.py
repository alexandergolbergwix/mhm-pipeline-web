"""Unit tests for HMO schema AI verify entry filtering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.pipeline import hmo_schema_actions
from app.pipeline import hmo_schema_verify as hsv
from app.pipeline.hmo_schema_bootstrap import SchemaBootstrapEntry, SchemaBootstrapResult
from app.pipeline.hmo_schema_verify import filter_schema_entries


def _report(*statuses: str) -> SchemaBootstrapResult:
    entries = [
        SchemaBootstrapEntry(
            ontology_uri=f"http://example.org#{status}",
            entity_kind="property",
            label=status,
            wikibase_id="P1" if status == "skipped" else None,
            status=status,
            message="",
        )
        for status in statuses
    ]
    return SchemaBootstrapResult(
        dry_run=True,
        created=0,
        skipped=sum(1 for s in statuses if s == "skipped"),
        failed=sum(1 for s in statuses if s == "failed"),
        would_create=sum(1 for s in statuses if s == "would_create"),
        entries=entries,
    )


def test_schema_verdict_query_summary_includes_datatype() -> None:
    entry = {
        "ontology_uri": "http://example.org#is_factual",
        "entity_kind": "property",
        "label": "is factual",
        "datatype": "boolean",
        "wikibase_id": "P204",
        "status": "skipped",
    }
    summary = hsv.schema_verdict_query_summary(entry, "gemini-3.5-flash")
    assert summary["datatype"] == "boolean"


def test_filter_schema_entries_includes_skipped_rows() -> None:
    report = _report("skipped", "would_create", "failed", "created")

    items = filter_schema_entries(report, ontology_uris=None)

    assert {item["status"] for item in items} == {
        "skipped",
        "would_create",
        "failed",
        "created",
    }


def test_filter_schema_entries_honours_ontology_uri_selection() -> None:
    report = _report("skipped", "would_create")

    items = filter_schema_entries(
        report,
        ontology_uris=["http://example.org#skipped"],
    )

    assert len(items) == 1
    assert items[0]["ontology_uri"] == "http://example.org#skipped"


def _audit_action() -> Any:
    action = hmo_schema_actions.get_action("audit_schema_entry")
    assert action is not None
    return action


async def _drain(agen: Any) -> list[Any]:
    return [ev async for ev in agen]


def _cached_pair(uri: str, status: str = "skipped") -> tuple[dict[str, Any], dict[str, Any]]:
    item = {
        "ontology_uri": uri,
        "entity_kind": "property",
        "label": uri.rsplit("#", 1)[-1],
        "status": status,
        "_local_id": f"property::{uri}",
    }
    payload = {
        "verdict": {"overall": "pass"},
        "judge_id": "gemini-3.5-flash",
        "judged_at": "2026-07-01T00:00:00Z",
        "cache_key": "abc",
    }
    return item, payload


@pytest.mark.asyncio
async def test_event_stream_skips_subprocess_when_everything_is_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the Redis/Postgres-cache-doesn't-work report: when
    every schema entry in scope is already an inference-cache hit, the
    eval-agent subprocess must never be spawned.
    """
    monkeypatch.setattr(hsv, "resolve_verify_state_dir", lambda *_a, **_k: tmp_path / "state")
    monkeypatch.setattr(
        hsv, "resolve_verify_session_dir", lambda *_a, **_k: tmp_path / "session",
    )
    monkeypatch.setattr(hsv, "persist_session_event", lambda *_a, **_k: None)
    monkeypatch.setattr(hsv, "read_run_verdicts", lambda *_a, **_k: [])

    spawned = {"called": False}

    async def _fake_spawn(**_kwargs: Any) -> Any:
        spawned["called"] = True
        if False:  # pragma: no cover - keeps this an async generator
            yield

    monkeypatch.setattr(hsv, "spawn_eval_agent_run", _fake_spawn)

    item, payload = _cached_pair("http://example.org#cached")

    events = await _drain(hsv.hmo_schema_verify_event_stream(
        run_id="r1",
        session_id="s1",
        action=_audit_action(),
        items=[item],
        uncached_items=[],
        pre_cached=[(item, payload)],
        api_key="key",
        override_cache=False,
        tier_model=None,
    ))
    assert spawned["called"] is False
    types = [ev.type for ev in events]
    assert types[0] == "session.start"
    assert types.count("agent.verdict") == 1
    assert types[-1] == "session.end"
    fixture_path = tmp_path / "session" / "pipeline-output" / "hmo_wikibase_schema.json"
    assert not fixture_path.exists()


@pytest.mark.asyncio
async def test_event_stream_fixture_excludes_precached_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The eval-agent fixture must only contain entries that missed the
    inference cache — otherwise the subprocess re-judges (and burns a
    fresh Gemini call for) rows the web tier already has a verdict for.
    """
    monkeypatch.setattr(hsv, "resolve_verify_state_dir", lambda *_a, **_k: tmp_path / "state")
    monkeypatch.setattr(
        hsv, "resolve_verify_session_dir", lambda *_a, **_k: tmp_path / "session",
    )
    monkeypatch.setattr(hsv, "persist_session_event", lambda *_a, **_k: None)
    monkeypatch.setattr(hsv, "locate_eval_agent", lambda: Path("/fake/eval-agent"))
    monkeypatch.setattr(hsv, "read_run_verdicts", lambda *_a, **_k: [])

    captured: dict[str, Any] = {}

    async def _fake_spawn(*, pipeline_output: Path, **_kwargs: Any) -> Any:
        captured["fixture"] = json.loads(
            (pipeline_output / "hmo_wikibase_schema.json").read_text(encoding="utf-8"),
        )
        if False:  # pragma: no cover - keeps this an async generator
            yield

    monkeypatch.setattr(hsv, "spawn_eval_agent_run", _fake_spawn)

    cached_item, cached_payload = _cached_pair("http://example.org#cached")
    uncached_item = {
        "ontology_uri": "http://example.org#fresh",
        "entity_kind": "property",
        "label": "fresh",
        "status": "created",
        "_local_id": "property::http://example.org#fresh",
    }

    await _drain(hsv.hmo_schema_verify_event_stream(
        run_id="r1",
        session_id="s1",
        action=_audit_action(),
        items=[cached_item, uncached_item],
        uncached_items=[uncached_item],
        pre_cached=[(cached_item, cached_payload)],
        api_key="key",
        override_cache=False,
        tier_model=None,
    ))

    fixture_uris = {entry["ontology_uri"] for entry in captured["fixture"]["entries"]}
    assert fixture_uris == {"http://example.org#fresh"}
