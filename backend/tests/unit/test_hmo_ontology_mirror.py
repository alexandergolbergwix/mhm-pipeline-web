from app.pipeline.hmo_ontology_mirror import compare_ontology_mirror


def test_ontology_mirror_reports_missing_and_extra_terms() -> None:
    result = compare_ontology_mirror(["a", "b"], ["a", "legacy"])
    assert result["mirrored"] is False
    assert result["missing_examples"] == ["b"]
    assert result["extra_examples"] == ["legacy"]
