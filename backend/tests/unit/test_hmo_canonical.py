import pytest

from app.pipeline.hmo_canonical import (
    assert_canonical_entities,
    normalize_live_entity,
)


def _raw(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "local_id": "Person_A",
        "source_uri": "https://w3id.org/mhm/ontology#Person_A",
        "wikibase_id": "Q1252",
        "revision_id": 7,
        "labels": {"en": "A"},
        "descriptions": {"en": "person"},
        "aliases": {},
        "claims": [],
        "authority_evidence": [],
    }
    value.update(overrides)
    return value


def test_normalize_live_entity_is_deterministic() -> None:
    first = normalize_live_entity(_raw())
    second = normalize_live_entity(_raw(revision_id=99))
    assert first.source_fingerprint == second.source_fingerprint
    assert first.to_dict()["wikibase_id"] == "Q1252"


def test_normalize_requires_identity_fields() -> None:
    with pytest.raises(ValueError, match="source_uri"):
        normalize_live_entity(_raw(source_uri=""))


def test_canonical_entities_reject_duplicate_wikibase_ids() -> None:
    first = normalize_live_entity(_raw())
    second = normalize_live_entity(_raw(local_id="Person_B", source_uri="https://w3id.org/mhm/ontology#Person_B"))
    with pytest.raises(ValueError, match="wikibase_id"):
        assert_canonical_entities([first, second])
