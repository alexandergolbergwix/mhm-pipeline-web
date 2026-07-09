"""Tests for wikibaseintegrator datatype shims and claim building."""

from __future__ import annotations

import pytest

from app.pipeline.hmo_item_upload import _build_wbi_claim
from converter.wikibase.resolved_models import ResolvedClaim
from converter.wikibase.wbi_datatypes import Boolean


@pytest.mark.parametrize(
    ("claim", "expected_type"),
    [
        (ResolvedClaim("P1", "wikibase-item", "Q42"), "wikibase-entityid"),
        (ResolvedClaim("P2", "string", "hello"), "string"),
        (ResolvedClaim("P3", "url", "https://example.com/x"), "string"),
        (ResolvedClaim("P4", "external-id", "NLI123"), "string"),
        (
            ResolvedClaim("P5", "monolingualtext", {"text": "שלום", "language": "he"}),
            "monolingualtext",
        ),
        (
            ResolvedClaim("P6", "monolingualtext", {"text": "note", "language": "und"}),
            "monolingualtext",
        ),
        (
            ResolvedClaim("P7", "time", {"time": "+1850-00-00T00:00:00Z", "precision": 9}),
            "time",
        ),
        (ResolvedClaim("P8", "quantity", {"amount": 32.0853}), "quantity"),
        (ResolvedClaim("P9", "boolean", True), "boolean"),
        (ResolvedClaim("P10", "boolean", False), "boolean"),
    ],
)
def test_build_wbi_claim_supports_all_schema_datatypes(
    claim: ResolvedClaim,
    expected_type: str,
) -> None:
    built = _build_wbi_claim(claim)
    assert built.mainsnak.datavalue["type"] == expected_type


def test_boolean_datatype_uses_wikibase_string_values() -> None:
    claim = Boolean(prop_nr="P42", value=True)
    assert claim.mainsnak.datavalue == {"value": "1", "type": "boolean"}

    claim_false = Boolean(prop_nr="P42", value=False)
    assert claim_false.mainsnak.datavalue == {"value": "0", "type": "boolean"}


def test_monolingual_claim_maps_und_language_to_en() -> None:
    claim = _build_wbi_claim(
        ResolvedClaim("P5", "monolingualtext", {"text": "note", "language": "und"}),
    )
    assert claim.mainsnak.datavalue["value"]["language"] == "en"
