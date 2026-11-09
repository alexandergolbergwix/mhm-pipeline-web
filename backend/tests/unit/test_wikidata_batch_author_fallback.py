"""Batch uploads must not reattribute sourced authors to a name string."""

from __future__ import annotations

import pytest

from app.pipeline import wikidata_upload
from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.uploader import (
    WikidataUploader,
    partition_unresolved_local,
    resolve_statement_locals,
)


@pytest.mark.parametrize("metadata", ["none", "reference", "blocks", "qualifier"])
@pytest.mark.parametrize("use_preflight", [False, True])
def test_batch_skipped_author_keeps_claim_evidence(metadata, use_preflight, monkeypatch):
    reference = {"property": "P248", "value": "Q1234", "type": "item"}
    qualifier = {"property": "P1932", "value": "Author", "type": "string"}
    claim = WikidataStatement(
        "P50", "__LOCAL:author", "item",
        references=[reference] if metadata in {"reference", "blocks"} else [],
        reference_blocks=[[reference]] if metadata == "blocks" else [],
        qualifiers=[qualifier] if metadata == "qualifier" else [],
    )
    work = WikidataItem(
        local_id="work", entity_type="work", labels={"en": "Work"},
        statements=[WikidataStatement("P31", "Q47461344", "item"), claim],
        work_candidate_evidence=[{"accepted": True, "source_field": "505"}],
    )
    author = WikidataItem(local_id="author", entity_type="person", labels={"en": "Author"})
    items = [work, author]
    preflight = (
        wikidata_upload.build_upload_corpus_quality_preflight(items) if use_preflight else None
    )
    if preflight is not None:
        assert preflight.decisions["work"].eligible
        assert not preflight.execution_findings
    monkeypatch.setattr(
        wikidata_upload, "_reconcile_for_upload", lambda *args, **kwargs: (None, "none"),
    )

    prepared = wikidata_upload.prepare_items_for_upload(
        items, object(), is_test=True, quality_preflight=preflight,
    )
    assert prepared[1].block_status == "skipped"
    actual = work.statements[-1]
    if metadata == "none":
        assert (actual.property_id, actual.value) == ("P2093", "Author")
        return

    assert actual == claim
    assert (actual.property_id, actual.value) == ("P50", "__LOCAL:author")
    if not use_preflight:
        assert prepared[0].blocked
    else:
        assert not prepared[0].blocked

    first_pass, deferred = partition_unresolved_local(work, {})
    assert claim not in first_pass.statements
    assert deferred == [claim]
    unresolved, missing = resolve_statement_locals(deferred[0], {})
    assert unresolved == claim
    assert missing == ["__LOCAL:author"]
    resolved, missing = resolve_statement_locals(deferred[0], {"author": "Q500"})
    assert missing == []
    assert resolved.value == "Q500"
    assert resolved.qualifiers == claim.qualifiers
    assert resolved.references == claim.references
    assert resolved.reference_blocks == claim.reference_blocks

    uploader = WikidataUploader(
        token="Fixture@Bot:not-a-credential", is_test=True, allow_live=False,
    )
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(uploader, "_init_wbi", lambda: None)
    result = uploader.upload_item(work, created_qids={})
    assert result.status == "blocked"
    assert "W-191" in result.message
