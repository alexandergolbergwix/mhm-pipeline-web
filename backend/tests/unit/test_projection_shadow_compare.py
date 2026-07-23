from pathlib import Path

from rdflib import Graph, Literal, URIRef

from app.pipeline.projection_shadow_compare import compare_item_projections, compare_rdf_projections


def _write(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def test_shadow_compare_reports_exact_and_different_projections(tmp_path: Path) -> None:
    same = "<https://example.test/a> <https://example.test/p> \"x\" ."
    changed = "<https://example.test/a> <https://example.test/p> \"y\" ."
    left = tmp_path / "legacy.ttl"
    right = tmp_path / "canonical.ttl"
    _write(left, same)
    _write(right, same)
    assert compare_rdf_projections(left, right)["identical"] is True
    _write(right, changed)
    result = compare_rdf_projections(left, right)
    assert result["identical"] is False
    assert result["only_legacy"] == 1
    assert result["only_canonical"] == 1


def test_item_shadow_compare_blocks_missing_canonical_data() -> None:
    result = compare_item_projections(
        [{"local_id": "a", "source_uri": "urn:a"}],
        [],
    )
    assert result["identical"] is False
    assert result["counts_by_kind"] == {"missing_canonical_data": 1}
    assert result["blocking_differences"] == 1
    assert result["promotion_ready"] is False


def test_item_shadow_compare_classifies_identifier_and_claim_conflicts() -> None:
    legacy = [{"local_id": "a", "hmo_wikibase_id": "Q1", "claims": [{"property_id": "P1", "value": "x"}]}]
    canonical = [{"local_id": "a", "hmo_wikibase_id": "Q2", "claims": [{"property_id": "P1", "value": "y"}]}]
    result = compare_item_projections(legacy, canonical)
    assert result["counts_by_kind"] == {"identifier_conflict": 1, "claim_conflict": 1}
    assert result["promotion_ready"] is False


def test_item_shadow_compare_allows_equal_projection_fields() -> None:
    item = {"local_id": "a", "labels": {"en": "A"}, "projection_source": "hmo_wikibase"}
    result = compare_item_projections([item], [dict(item)])
    assert result["identical"] is True
    assert result["blocking_differences"] == 0
    assert result["promotion_ready"] is True
