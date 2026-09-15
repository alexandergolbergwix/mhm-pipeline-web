"""Verify event streams must not yield inside ``finally`` during close.

Cancelling a verify job closes the event stream (``GeneratorExit``). The
stream's ``finally`` persists on-disk verdicts and used to ``yield`` them
too — illegal while ``GeneratorExit`` is pending, so the close raised
``RuntimeError: async generator ignored GeneratorExit`` and the job died
*failed* with every verdict lost (job 993884a7, 2026-09-15).
"""

from __future__ import annotations

import asyncio

import pytest

from app.pipeline.agent_runner import AgentEvent, generator_is_closing


def test_generator_is_closing_only_under_generator_exit() -> None:
    assert not generator_is_closing()

    seen: list[bool] = []

    async def gen() -> "asyncio.AsyncIterator[int]":
        try:
            yield 1
        finally:
            seen.append(generator_is_closing())

    async def drive() -> None:
        g = gen()
        await g.__anext__()
        await g.aclose()  # raises if the finally yields

    asyncio.run(drive())
    assert seen == [True]


def test_finally_may_await_but_not_yield_while_closing() -> None:
    """The persisted-cleanup pattern: await in finally is fine, yield is not."""

    async def gen() -> "asyncio.AsyncIterator[int]":
        try:
            yield 1
        finally:
            await asyncio.sleep(0)  # persistence-style await
            # A yield here would raise RuntimeError during aclose().

    async def drive() -> None:
        g = gen()
        assert await g.__anext__() == 1
        await g.aclose()

    asyncio.run(drive())


@pytest.mark.parametrize("stream_module", [
    "app.routers.extraction_verify",
    "app.routers.ai_verify",
    "app.routers.wikidata_studio",
    "app.pipeline.hmo_item_verify",
    "app.pipeline.hmo_schema_verify",
])
def test_verify_stream_sources_import_generator_guard(stream_module: str) -> None:
    """Every verify channel imports the closing guard (fix is wired in)."""
    import importlib

    module = importlib.import_module(stream_module)
    source = open(module.__file__, encoding="utf-8").read()
    assert "generator_is_closing" in source, (
        f"{stream_module} must guard finally-block yields with generator_is_closing"
    )
