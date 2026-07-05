"""Tests for mapping SHACL violations onto HMO item local_ids.

Uses a synthetic ``ShaclReport`` (rather than the real pyshacl engine)
so the mapping logic is pinned independently of the shapes file content.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline import hmo_item_shacl
from app.pipeline.rdf_build import ShaclReport, ShaclViolation

_ENTITIES = [
    {"local_id": "QDraft_MS1", "source_uri": "http://example.org/MS1"},
    {"local_id": "QDraft_MS2", "source_uri": "http://example.org/MS2"},
]


@pytest.mark.asyncio
async def test_violation_on_known_focus_node_maps_to_local_id() -> None:
    report = ShaclReport(
        conforms=False,
        violations=[
            ShaclViolation(
                focus_node="http://example.org/MS1",
                source_shape="hm:ManuscriptShape",
                severity="Violation",
                message="Missing required title",
                value=None,
            ),
        ],
    )
    with patch.object(hmo_item_shacl, "validate_with_shacl", AsyncMock(return_value=report)):
        result = await hmo_item_shacl.build_shacl_report_for_items(Path("unused.ttl"), _ENTITIES)

    assert list(result.keys()) == ["QDraft_MS1"]
    assert result["QDraft_MS1"][0]["message"] == "Missing required title"
    assert result["QDraft_MS1"][0]["severity"] == "Violation"


@pytest.mark.asyncio
async def test_conforming_graph_yields_empty_report() -> None:
    report = ShaclReport(conforms=True, violations=[])
    with patch.object(hmo_item_shacl, "validate_with_shacl", AsyncMock(return_value=report)):
        result = await hmo_item_shacl.build_shacl_report_for_items(Path("unused.ttl"), _ENTITIES)

    assert result == {}


@pytest.mark.asyncio
async def test_violation_on_unknown_focus_node_is_dropped_silently() -> None:
    report = ShaclReport(
        conforms=False,
        violations=[
            ShaclViolation(
                focus_node="http://example.org/NotAnItem",
                source_shape="",
                severity="Violation",
                message="orphan violation",
                value=None,
            ),
        ],
    )
    with patch.object(hmo_item_shacl, "validate_with_shacl", AsyncMock(return_value=report)):
        result = await hmo_item_shacl.build_shacl_report_for_items(Path("unused.ttl"), _ENTITIES)

    assert "QDraft_MS1" not in result
    assert "QDraft_MS2" not in result


@pytest.mark.asyncio
async def test_shacl_failure_degrades_to_empty_report_without_raising() -> None:
    with patch.object(
        hmo_item_shacl, "validate_with_shacl", AsyncMock(side_effect=RuntimeError("bad turtle")),
    ):
        result = await hmo_item_shacl.build_shacl_report_for_items(Path("unused.ttl"), _ENTITIES)

    assert result == {}
