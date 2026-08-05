"""The export-quality gate runs in the build path, on both sources (Rule W-163).

The canonical branch once returned before ever calling the gate, so every
identity and evidence defect in the canonical projection reached the Studio cache
unchecked. These tests pin the gate to the build path itself, not to the export.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.hmo_canonical import CanonicalHmoEntity
from app.routers import wikidata_studio as router

_CN = "990000403370205171"


def _marc_row(cn: str = _CN) -> SimpleNamespace:
    return SimpleNamespace(control_number=cn, marc={"control_number": cn, "title": "t"})


@pytest.fixture
def _build_env():
    """Patch everything `execute_studio_build` touches except the gate."""
    canonical_entity = CanonicalHmoEntity(
        local_id="QDraft_MS_1",
        source_uri=f"https://example.org/marc/{_CN}",
        wikibase_id="Q1",
        revision_id=1,
        labels={"en": f"NLI, {_CN}"},
        descriptions={},
        aliases={},
        claims=[],
        authority_evidence=[],
        source_fingerprint="fp",
        entity_type="manuscript",
        control_numbers=[_CN],
    )
    native_item = SimpleNamespace(local_id="QDraft_MS_1", entity_type="manuscript")
    serialised = [{"local_id": "QDraft_MS_1", "entity_type": "manuscript"}]
    cache_row = SimpleNamespace(id=uuid.uuid4(), result_items=serialised)

    with (
        patch.object(
            router, "_load_studio_build_rows",
            new=AsyncMock(return_value=([_marc_row()], [], [], [])),
        ),
        patch.object(
            router.wikidata_studio, "hmo_instance_qids_for_run",
            new=AsyncMock(return_value={}),
        ),
        patch.object(
            router, "_canonical_entities_for_run",
            new=AsyncMock(return_value=[canonical_entity]),
        ),
        patch.object(router, "_get_studio_cache_row", new=AsyncMock(return_value=None)),
        patch.object(router, "_prewarm_transliterations", new=AsyncMock(return_value={})),
        patch.object(
            router.wikidata_studio, "build_items_for_run",
            new=AsyncMock(return_value={
                "items": serialised, "native_items": [native_item],
                "quickstatements": "", "summary": {},
            }),
        ),
        patch.object(
            router, "build_canonical_studio_result",
            return_value={
                "items": serialised, "native_items": [native_item],
                "quickstatements": "", "summary": {},
            },
        ) as build,
        patch.object(router, "_upsert_studio_cache", new=AsyncMock()) as upsert,
    ):
        # The cache row is read twice: once for the staleness check (None) and
        # once after the upsert (the fresh row).
        router._get_studio_cache_row.side_effect = [None, cache_row, cache_row]
        yield SimpleNamespace(
            build=build, upsert=upsert, native_item=native_item, serialised=serialised,
        )


@pytest.mark.asyncio
async def test_canonical_build_runs_the_quality_gate_before_caching(_build_env) -> None:
    calls: list[str] = []

    def _gate(items, **kwargs):
        calls.append("gate")
        assert items == [_build_env.native_item]
        # The evidence-level checks need the serialised shape and the MARC slice.
        assert kwargs["serialised_items"] == _build_env.serialised
        assert kwargs["marc_records"] and kwargs["marc_records"][0]["control_number"]

    async def _upsert(*_args, **_kwargs):
        calls.append("upsert")

    _build_env.upsert.side_effect = _upsert

    with patch.object(router, "assert_wikidata_export_quality", side_effect=_gate):
        await router.execute_studio_build(
            AsyncMock(), run_id=uuid.uuid4(), approved_only=True, source="canonical",
            force_rebuild=True, run_user_id=None, reconcile=False,
        )

    assert calls == ["gate", "upsert"], (
        "the canonical branch must gate the projection before it reaches the cache"
    )


@pytest.mark.asyncio
async def test_canonical_build_requests_native_items_for_the_gate(_build_env) -> None:
    with patch.object(router, "assert_wikidata_export_quality"):
        await router.execute_studio_build(
            AsyncMock(), run_id=uuid.uuid4(), approved_only=True, source="canonical",
            force_rebuild=True, run_user_id=None, reconcile=False,
        )

    # Without return_native the gate would receive None and check nothing.
    assert _build_env.build.call_args.kwargs["return_native"] is True


@pytest.mark.asyncio
async def test_a_blocking_finding_stops_the_canonical_build(_build_env) -> None:
    with (
        patch.object(
            router, "assert_wikidata_export_quality",
            side_effect=ValueError("MISSING_LABEL QDraft_MS_1"),
        ),
        pytest.raises(ValueError, match="MISSING_LABEL"),
    ):
        await router.execute_studio_build(
            AsyncMock(), run_id=uuid.uuid4(), approved_only=True, source="canonical",
            force_rebuild=True, run_user_id=None, reconcile=False,
        )

    _build_env.upsert.assert_not_awaited()
