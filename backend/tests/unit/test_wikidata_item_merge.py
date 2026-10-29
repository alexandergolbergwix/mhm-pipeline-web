"""The drawer accept binds an existing QID everywhere the item is judged."""

from __future__ import annotations

from app.pipeline.wikidata_item_merge import apply_wikidata_item_override


def test_drawer_accept_binds_existing_qid_when_absent() -> None:
    out = apply_wikidata_item_override(
        {"local_id": "QDraft_Person_976", "statements": []},
        {"accepted_foreign_qid": "Q1176777", "accept_foreign_modify": True},
    )
    assert out["existing_qid"] == "Q1176777"


def test_drawer_accept_never_replaces_an_existing_binding() -> None:
    out = apply_wikidata_item_override(
        {"local_id": "QDraft_Person_1", "existing_qid": "Q1", "statements": []},
        {"accepted_foreign_qid": "Q2"},
    )
    assert out["existing_qid"] == "Q1"
