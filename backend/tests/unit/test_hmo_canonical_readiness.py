from app.pipeline.hmo_canonical_readiness import check


def test_readiness_blocks_missing_live_state() -> None:
    result = check([{"local_id": "A", "source_uri": "u", "wikibase_id": "Q1"}])
    assert result["ready"] is False
    assert result["missing_canonical_live"] == 1


def test_readiness_accepts_complete_unique_snapshot() -> None:
    result = check([{
        "local_id": "A", "source_uri": "u", "wikibase_id": "Q1",
        "canonical_live": {"wikibase_id": "Q1"},
    }])
    assert result["ready"] is True
