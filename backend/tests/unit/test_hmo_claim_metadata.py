"""HMO claim metadata survives source choices and the actual writer payload."""

from copy import deepcopy

import pytest

from app.pipeline.hmo_item_upload import (
    _build_wbi_claim,
    _payload_fingerprint,
    _sanitize_url_claim,
    _string_compatible_claim,
)
from converter.wikibase.resolved_models import ResolvedClaim, ResolvedWikibaseEntity


def _source_choice() -> dict:
    return {
        "property_id": "P276",
        "datatype": "string",
        "value": "Q100",
        "rank": "preferred",
        "qualifiers": [
            {"property": "P82", "type": "string", "value": "990001028160205171"},
            {"property": "P82", "type": "string", "value": "990035802410205171"},
        ],
        "reference_blocks": [
            [{"property": "P84", "type": "url", "value": "https://example.org/source-one"}],
            [{"property": "P84", "type": "url", "value": "https://example.org/source-two"}],
        ],
    }


def test_claim_metadata_survives_serialization_and_wbi_conversion() -> None:
    source = _source_choice()
    claim = ResolvedClaim.from_dict(source)
    assert claim.to_dict() == source
    wire = _build_wbi_claim(claim).get_json()
    assert wire["rank"] == "preferred"
    assert [q["datavalue"]["value"] for q in wire["qualifiers"]["P82"]] == [
        "990001028160205171",
        "990035802410205171",
    ]
    assert len(wire["references"]) == 2
    assert [r["snaks"]["P84"][0]["datavalue"]["value"] for r in wire["references"]] == [
        "https://example.org/source-one",
        "https://example.org/source-two",
    ]
    assert source == _source_choice()


def test_source_scope_and_reference_changes_invalidate_the_write_fingerprint() -> None:
    def fingerprint(raw: dict) -> str:
        entity = ResolvedWikibaseEntity(
            local_id="person",
            source_uri="https://example.org/person",
            labels={},
            descriptions={},
            class_qid="Q1",
            claims=[ResolvedClaim.from_dict(raw)],
        )
        return _payload_fingerprint(entity, {}, {}, {})

    original = _source_choice()
    original_hash = fingerprint(original)
    for field in ["qualifiers", "reference_blocks"]:
        changed = deepcopy(original)
        changed[field] = changed[field][:1]
        assert fingerprint(changed) != original_hash
    changed = {**original, "rank": "normal"}
    assert fingerprint(changed) != original_hash


def test_scalar_and_url_coercion_preserve_claim_metadata() -> None:
    source = _source_choice()
    boolean = ResolvedClaim.from_dict({**source, "datatype": "boolean", "value": True})
    assert _string_compatible_claim(boolean).to_dict() == {**source, "value": "true"}
    url = ResolvedClaim.from_dict({**source, "datatype": "url", "value": '"https://example.org/"'})
    cleaned = _sanitize_url_claim(url)
    assert cleaned is not None
    assert cleaned.to_dict() == {**source, "datatype": "url", "value": "https://example.org/"}


def test_unsupported_metadata_stops_before_the_writer() -> None:
    source = _source_choice()
    source["qualifiers"] = [{"property": "P82", "type": "wikibase-item", "value": "Q123"}]
    with pytest.raises(ValueError, match="metadata"):
        _build_wbi_claim(ResolvedClaim.from_dict(source))


def test_time_metadata_preserves_uncertainty_and_calendar() -> None:
    source = _source_choice()
    when = {
        "time": "+1500-00-00T00:00:00Z",
        "precision": 9,
        "timezone": 0,
        "before": 1,
        "after": 2,
        "calendarmodel": "http://www.wikidata.org/entity/Q1985786",
    }
    source["qualifiers"] = [{"property": "P85", "type": "time", "value": when}]
    wire = _build_wbi_claim(ResolvedClaim.from_dict(source)).get_json()
    assert wire["qualifiers"]["P85"][0]["datavalue"]["value"] == when
