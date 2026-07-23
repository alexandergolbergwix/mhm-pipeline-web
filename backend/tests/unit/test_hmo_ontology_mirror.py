from app.pipeline.hmo_ontology_mirror import compare_ontology_mirror


def test_ontology_mirror_reports_missing_and_extra_terms() -> None:
    result = compare_ontology_mirror(["a", "b"], ["a", "legacy"])
    assert result["mirrored"] is False
    assert result["missing_examples"] == ["b"]
    assert result["extra_examples"] == ["legacy"]


def test_ontology_mirror_reports_label_and_datatype_drift() -> None:
    result = compare_ontology_mirror(
        ["p"],
        ["p"],
        expected_metadata={"p": {"label": "New label", "datatype": "url"}},
        mapped_metadata={"p": {"label": "Old label", "datatype": "string"}},
    )
    assert result["drifted"] is True
    assert result["label_drift_examples"] == ["p"]
    assert result["datatype_drift_examples"] == ["p"]
