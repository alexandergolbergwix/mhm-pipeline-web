"""Wikidata Studio build-fingerprint contracts."""

from types import SimpleNamespace

from app.pipeline.wikidata_studio import compute_build_fingerprint


def _record(marc: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(control_number="990001", marc=marc)


def test_marc_content_change_invalidates_build_fingerprint() -> None:
    before = compute_build_fingerprint(
        [_record({"500$a": "כולל: ספר א"})],
        [],
        [],
        [],
        True,
    )
    after = compute_build_fingerprint(
        [_record({"500$a": "כולל: ספר ב"})],
        [],
        [],
        [],
        True,
    )
    assert before != after


def test_marc_transport_metadata_does_not_change_build_fingerprint() -> None:
    before = compute_build_fingerprint(
        [_record({
            "dates": {"year": 1600},
            "created_at": "2026-08-27T00:00:00Z",
            "updated_at": "2026-08-27T00:00:00Z",
        })],
        [],
        [],
        [],
        True,
    )
    after = compute_build_fingerprint(
        [_record({
            "dates": {"year": 1600},
            "created_at": "2026-08-28T00:00:00Z",
            "updated_at": "2026-08-28T00:00:00Z",
        })],
        [],
        [],
        [],
        True,
    )
    assert before == after


def test_semantic_marc_date_change_invalidates_build_fingerprint() -> None:
    before = compute_build_fingerprint(
        [_record({"dates": {"year": 1600}})], [], [], [], True,
    )
    after = compute_build_fingerprint(
        [_record({"dates": {"year": 1700}})], [], [], [], True,
    )
    assert before != after
