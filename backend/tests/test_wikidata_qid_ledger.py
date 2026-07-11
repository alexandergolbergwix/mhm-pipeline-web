"""Tests for the global Wikidata QID ledger."""

from __future__ import annotations

import uuid

import pytest

from app.models.wikibase_entity_mapping import ENTITY_KIND_INSTANCE
from app.pipeline.wikidata_qid_ledger import (
    ledger_key_for_item,
    load_global_ledger,
    lookup_ledger_qid,
    record_ledger_mapping,
)


def test_ledger_key_for_manuscript() -> None:
    key = ledger_key_for_item(
        {"entity_type": "manuscript", "local_id": "990001234"},
        ns="wikidata",
    )
    assert key == "wikidata:marc:990001234"


@pytest.mark.asyncio
async def test_record_and_load_global_ledger(db_session) -> None:
    key = "wikidata:person:person::viaf-1"
    await record_ledger_mapping(
        db_session, key, "Q500", local_key="person::viaf-1", label="Author",
    )
    ledger = await load_global_ledger(db_session)
    assert lookup_ledger_qid(ledger, key) == "Q500"


@pytest.mark.asyncio
async def test_prepare_skips_reconcile_when_ledger_hits() -> None:
    from dataclasses import dataclass, field

    from app.pipeline import wikidata_upload as wu

    @dataclass
    class _Item:
        entity_type: str = "manuscript"
        labels: dict = field(default_factory=lambda: {"en": "MS"})
        statements: list = field(default_factory=list)
        existing_qid: str = ""
        local_id: str = "990009999"

    class _Rec:
        def __init__(self) -> None:
            self.ms_calls: list = []

        def reconcile_manuscript_by_identifiers(self, nnl_id, shelfmark):
            self.ms_calls.append((nnl_id, shelfmark))
            return None

    item = _Item()
    key = ledger_key_for_item(item, ns="wikidata")
    reconciler = _Rec()
    prepared = wu._prepare_for_upload(
        [item], reconciler, ledger={key: "Q777"}, ledger_ns="wikidata",
    )
    assert prepared[0].existing_qid == "Q777"
    assert prepared[0].method == "ledger"
    assert reconciler.ms_calls == []
