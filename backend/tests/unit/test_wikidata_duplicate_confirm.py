"""Uncertain-duplicate confirm and pass-2 ownership (Rule W-195)."""

from __future__ import annotations

from converter.wikidata.item_models import WikidataStatement
from app.pipeline.wikidata_duplicate_confirm import (
    DIFFERENT_ITEM,
    SAME_ITEM,
    UNSURE,
    confirm_uncertain_duplicate,
)
from app.pipeline.wikidata_upload import (
    ForeignAccept,
    PreparedItem,
    UploadOutcome,
    _apply_existence_and_ownership,
    pass2_may_update_source,
    rewrite_author_link_to_name_string,
)


def test_confirm_unsure_on_garbage() -> None:
    assert confirm_uncertain_duplicate(
        local_id="p1",
        entity_type="person",
        heading="דוד סולל",
        candidate_qid="Q123",
        method="label",
        has_trusted_identifier=False,
        complete=lambda prompt: "not json",
    ) == UNSURE


def test_confirm_same_item_without_identifier_is_unsure() -> None:
    assert confirm_uncertain_duplicate(
        local_id="p1",
        entity_type="person",
        heading="דוד סולל",
        candidate_qid="Q123",
        method="label",
        has_trusted_identifier=False,
        complete=lambda prompt: '{"verdict": "same_item"}',
    ) == UNSURE


def test_confirm_same_item_with_identifier() -> None:
    assert confirm_uncertain_duplicate(
        local_id="p1",
        entity_type="person",
        heading="רש״י",
        candidate_qid="Q123",
        method="identifier",
        has_trusted_identifier=True,
        complete=lambda prompt: '{"verdict": "same_item"}',
    ) == SAME_ITEM


def test_confirm_tool_failure_is_unsure() -> None:
    def _boom(prompt: str) -> str:
        raise RuntimeError("network")

    assert confirm_uncertain_duplicate(
        local_id="p1",
        entity_type="work",
        heading="הגדה",
        candidate_qid="Q9",
        method="label+author",
        has_trusted_identifier=False,
        complete=_boom,
    ) == UNSURE


def test_uncertain_work_unsure_skips(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_labels",
        lambda qids, *, is_test=False: {"Q1": {"en": "Haggadah", "he": "הגדה"}},
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_duplicate_confirm.confirm_uncertain_duplicate",
        lambda **kwargs: UNSURE,
    )
    item = type("W", (), {
        "entity_type": "work",
        "existing_qid": "Q1",
        "labels": {"he": "הגדה"},
        "statements": [],
    })()
    prepared = PreparedItem(
        item=item,
        local_id="QDraft_Work_1",
        label="הגדה",
        entity_type="work",
        existing_qid="Q1",
        method="label+author",
        blocked=False,
        block_status="",
        block_message="",
    )
    out = _apply_existence_and_ownership(
        prepared,
        accept=ForeignAccept(),
        ownership_checker=None,
        is_test=False,
    )
    assert out.block_status == "skipped"
    assert out.existing_qid is None
    assert "W-195" in out.block_message


def test_identity_clash_does_not_call_confirm(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_labels",
        lambda qids, *, is_test=False: {
            "Q209579": {"en": "Victor Amadeus II of Savoy"},
        },
    )

    def _no_confirm(**kwargs):
        raise AssertionError("confirm must not run after a W-190 clash")

    monkeypatch.setattr(
        "app.pipeline.wikidata_duplicate_confirm.confirm_uncertain_duplicate",
        _no_confirm,
    )

    class _Foreign:
        def _is_our_item(self, qid: str) -> bool:
            return False

    item = type("P", (), {
        "entity_type": "person",
        "existing_qid": "Q209579",
        "labels": {"he": "ויטוריו אמדיאו"},
        "statements": [],
        "authority_evidence": [],
    })()
    prepared = PreparedItem(
        item=item,
        local_id="QDraft_Person_71",
        label="ויטוריו אמדיאו",
        entity_type="person",
        existing_qid="Q209579",
        method="prebuilt",
        blocked=False,
        block_status="",
        block_message="",
    )
    out = _apply_existence_and_ownership(
        prepared,
        accept=ForeignAccept(),
        ownership_checker=_Foreign(),
        is_test=False,
    )
    assert out.block_status == "skipped"


def test_pass2_may_update_created_without_ownership() -> None:
    created = UploadOutcome(
        local_id="w1", label="work", entity_type="work",
        qid="Q1", status="created", message="ok", added_properties=[],
    )
    assert pass2_may_update_source(created) is True


def test_pass2_refuses_foreign_update() -> None:
    foreign = UploadOutcome(
        local_id="w1", label="work", entity_type="work",
        qid="Q9", status="updated", message="ok", added_properties=[],
        ownership="foreign",
    )
    assert pass2_may_update_source(foreign) is False


def test_pass2_allows_own_update() -> None:
    own = UploadOutcome(
        local_id="w1", label="work", entity_type="work",
        qid="Q9", status="updated", message="ok", added_properties=[],
        ownership="own",
    )
    assert pass2_may_update_source(own) is True


def test_rewrite_p50_local_to_p2093() -> None:
    stmt = WikidataStatement(
        property_id="P50", value="__LOCAL:person:x", value_type="item",
    )
    rewritten = rewrite_author_link_to_name_string(stmt, "דוד סולל")
    assert rewritten.property_id == "P2093"
    assert rewritten.value == "דוד סולל"
    assert rewritten.value_type == "string"
    assert stmt.property_id == "P50"
