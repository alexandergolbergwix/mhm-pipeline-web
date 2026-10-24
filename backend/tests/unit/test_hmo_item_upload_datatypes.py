"""Claim coercion for live property datatypes (upload throughput)."""

from __future__ import annotations

import json

import pytest

from app.pipeline.hmo_item_upload import (
    _coerce_claims_to_live_datatypes,
    _fetch_live_property_datatypes,
)
from converter.wikibase.resolved_models import ResolvedClaim, ResolvedWikibaseEntity


def _entity(claims: list[ResolvedClaim]) -> ResolvedWikibaseEntity:
    return ResolvedWikibaseEntity(
        local_id="QDraft_P1",
        labels={"en": "x"},
        descriptions={},
        class_qid="Q2",
        source_uri="urn:hmo:p1",
        claims=claims,
    )


def test_coerce_shapes_scalars_for_live_string_properties() -> None:
    """A quantity on a property that exists as ``string`` must be sent as a
    string — the mismatch otherwise fails the write and the per-claim
    fallback rewrote the item once per claim (run 47e03437)."""
    entity = _entity([
        ResolvedClaim("P224", "quantity", {"amount": "3"}),
        ResolvedClaim("P100", "time", {"time": "+1500-00-00T00:00:00Z", "precision": 9}),
        ResolvedClaim("P224", "string", "keep"),
        ResolvedClaim("P3", "boolean", True),
    ])
    out = _coerce_claims_to_live_datatypes(
        entity, {"P224": "string", "P100": "time", "P3": "string"},
    )
    assert [(c.property_id, c.datatype, c.value) for c in out.claims] == [
        ("P224", "string", "3"),
        ("P100", "time", {"time": "+1500-00-00T00:00:00Z", "precision": 9}),
        ("P224", "string", "keep"),
        ("P3", "string", "true"),
    ]
    # The source entity is never mutated.
    assert entity.claims[0].datatype == "quantity"


def test_coerce_leaves_unknown_and_matching_properties_alone() -> None:
    entity = _entity([
        ResolvedClaim("P9", "quantity", {"amount": "1"}),
        ResolvedClaim("P8", "quantity", {"amount": "2"}),
    ])
    out = _coerce_claims_to_live_datatypes(entity, {"P8": "quantity"})
    assert [c.datatype for c in out.claims] == ["quantity", "quantity"]
    assert out is entity  # unchanged input returns the same object


@pytest.mark.asyncio
async def test_fetch_live_property_datatypes_batches_fifty(monkeypatch) -> None:
    calls: list[str] = []

    class _Resp:
        def __init__(self, body: dict) -> None:
            self._body = body

        def read(self) -> bytes:
            return json.dumps(self._body).encode()

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

    def fake_urlopen(url: str, timeout: float | None = None) -> _Resp:  # type: ignore[no-untyped-def]
        calls.append(url)
        ids = url.split("ids=")[1].split("&")[0].split("|")
        return _Resp({"entities": [{"id": i, "datatype": "string"} for i in ids]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = await _fetch_live_property_datatypes(
        "https://wiki.example", [f"P{i}" for i in range(60)],
    )
    assert out["P0"] == "string" and out["P59"] == "string"
    assert len(calls) == 2  # 50 + 10
