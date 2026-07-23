from app.pipeline.hmo_canonical import canonical_entity_fingerprint
from app.pipeline.hmo_canonical_readiness import CanonicalReadiness, evaluate


def _snapshot(local_id: str, source_uri: str, qid: str) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "local_id": local_id,
        "source_uri": source_uri,
        "wikibase_id": qid,
        "labels": {"en": local_id},
        "descriptions": {},
        "aliases": {},
        "claims": [],
        "authority_evidence": [],
    }
    snapshot["source_fingerprint"] = canonical_entity_fingerprint(snapshot)
    return snapshot


def test_readiness_reports_integrity_failures() -> None:
    first = _snapshot("A", "urn:a", "Q1")
    second = _snapshot("B", "urn:b", "Q1")
    expected = [
        {"local_id": "A", "canonical_live": first},
        {"local_id": "B", "canonical_live": second},
    ]
    rows = [
        {"local_id": "A", "source_uri": "urn:a", "wikibase_id": "Q1", "snapshot": first, "source_fingerprint": "stale"},
        {"local_id": "A", "source_uri": "urn:other", "wikibase_id": "Q1", "snapshot": {"local_id": "A"}, "source_fingerprint": ""},
    ]

    result = evaluate(
        expected,
        rows,
        authority_conflicts=[{"kind": "wikidata", "identifier": "Q1"}],
        live_readback_failures=[{"local_id": "B", "reason": "timeout"}],
    )

    assert result.ready is False
    assert result.expected_item_count == 2
    assert result.durable_row_count == 2
    assert result.missing_rows == ["B"]
    assert result.duplicate_wikibase_qids == ["Q1"]
    assert result.malformed_rows == [{"local_id": "A", "error": "canonical HMO entity missing required fields: source_uri, wikibase_id"}]
    assert result.authority_conflicts
    assert result.live_readback_failures


def test_readiness_requires_current_fingerprint() -> None:
    snapshot = _snapshot("A", "urn:a", "Q1")
    result = evaluate(
        [{"local_id": "A", "canonical_live": snapshot}],
        [{
            "local_id": "A",
            "source_uri": "urn:a",
            "wikibase_id": "Q1",
            "snapshot": snapshot,
            "source_fingerprint": snapshot["source_fingerprint"],
        }],
    )

    assert result.ready is True
    assert result.to_dict()["expected_item_count"] == 1


def test_readiness_round_trips_gate_envelope() -> None:
    result = CanonicalReadiness.from_dict({
        "runs": 1,
        "ready": True,
        "results": [{
            "expected_item_count": 1,
            "durable_row_count": 1,
            "missing_rows": [],
            "malformed_rows": [],
            "duplicate_local_ids": [],
            "duplicate_source_uris": [],
            "duplicate_wikibase_qids": [],
            "authority_conflicts": [],
            "stale_fingerprints": [],
            "missing_fingerprints": [],
            "live_readback_failures": [],
            "ready": True,
        }],
    })

    assert result.ready is True
    assert result.expected_item_count == 1