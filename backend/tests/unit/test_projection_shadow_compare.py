from pathlib import Path

from rdflib import Graph, Literal, URIRef

from app.pipeline.projection_shadow_compare import compare_rdf_projections


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
