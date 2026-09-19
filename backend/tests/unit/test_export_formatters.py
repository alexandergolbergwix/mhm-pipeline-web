"""Unit tests for app.export.formatters streaming helpers."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from uuid import UUID

from app.export.formatters import json_array_stream


async def _drain(agen) -> bytes:
    return b"".join([chunk async for chunk in agen])


async def _aiter(items) -> AsyncIterator[dict]:
    for item in items:
        yield item


async def test_json_array_stream_async_items_matches_full_dumps() -> None:
    body = await _drain(json_array_stream(
        {"run_id": "r1", "scope": "all"},
        "entities",
        _aiter([{"id": 1, "name": "א"}, {"id": 2, "name": "b"}]),
    ))
    assert json.loads(body) == {
        "run_id": "r1", "scope": "all",
        "entities": [{"id": 1, "name": "א"}, {"id": 2, "name": "b"}],
    }


async def test_json_array_stream_sync_and_empty_items() -> None:
    body = await _drain(json_array_stream({"run_id": "r2"}, "items", [{"x": 1}]))
    assert json.loads(body) == {"run_id": "r2", "items": [{"x": 1}]}

    empty = await _drain(json_array_stream({"a": 1}, "items", []))
    assert json.loads(empty) == {"a": 1, "items": []}


async def test_json_array_stream_defaults_uuid_and_datetime() -> None:
    when = datetime(2026, 9, 19, 12, 0, 0)
    body = await _drain(json_array_stream(
        {}, "items",
        [{"id": UUID("12345678-1234-5678-1234-567812345678"), "at": when}],
    ))
    assert json.loads(body)["items"][0] == {
        "id": "12345678-1234-5678-1234-567812345678",
        "at": "2026-09-19T12:00:00",
    }


async def test_json_array_stream_first_chunk_before_items() -> None:
    """The header prefix must be yielded before touching a slow item source."""

    async def slow():
        raise AssertionError("items must not be consumed for the first chunk")
        yield {}  # pragma: no cover

    gen = json_array_stream({"run_id": "r"}, "entities", slow())
    first = await gen.__anext__()
    assert json.dumps({"run_id": "r"})[:-1].encode() in first
