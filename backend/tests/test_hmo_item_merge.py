"""Tests for HMO Wikibase item review helpers."""

from __future__ import annotations

from app.pipeline.hmo_item_merge import apply_hmo_item_override


def test_apply_hmo_item_override_merges_labels_and_claims():
    entity = {
        "local_id": "QDraft_a",
        "labels": {"en": "Base"},
        "claims": [{"property_id": "P31", "datatype": "wikibase-item", "value": "Q1"}],
    }
    ov = {
        "labels": {"he": "בסיס"},
        "add_statements": [{"property_id": "P999", "datatype": "string", "value": "x"}],
    }
    merged = apply_hmo_item_override(entity, ov)
    assert merged["labels"]["en"] == "Base"
    assert merged["labels"]["he"] == "בסיס"
    assert len(merged["claims"]) == 2


def test_hmo_source_uri_constant_in_exporter():
    from converter.config.namespaces import HM
    from converter.wikibase.hmo_exporter import HMO_SOURCE_URI

    assert HMO_SOURCE_URI == f"{HM}hmo_source_uri"
