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
