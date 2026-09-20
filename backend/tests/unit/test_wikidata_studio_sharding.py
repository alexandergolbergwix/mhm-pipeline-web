"""Sharded Wikidata Studio build contracts (batch-build parity).

Pins the replacement of the single-dyno, whole-corpus build:

* ``shard_slices`` chunks control numbers in CN order;
* the streamed fingerprint is byte-identical to the sequential
  ``compute_build_fingerprint`` and still flips on any approval change;
* ``native_item_payload`` / ``native_item_from_payload`` round-trip a
  native ``WikidataItem`` losslessly through JSON;
* ``merge_shard_items`` reproduces ``build_all``'s corpus-wide person/
  work deduplication (first occurrence wins, ``records`` union,
  manuscripts never dedupe).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.models.extraction_approval import ExtractionApproval
from app.models.item_override import WikidataItemOverride
from app.models.run import AuthorityMatch, RunRecord
from app.pipeline.wikidata_studio import (
    compute_build_fingerprint,
    merge_shard_items,
    native_item_from_payload,
    native_item_payload,
)
from app.pipeline.wikidata_studio_batches import (
    compute_build_fingerprint_streamed,
    list_run_control_numbers,
    shard_slices,
)

# ── shard planning ──────────────────────────────────────────────────────


def test_shard_slices_chunks_in_order() -> None:
    cns = [f"cn{i:03d}" for i in range(7)]
    slices = shard_slices(cns, 3)
    assert slices == [
        ["cn000", "cn001", "cn002"],
        ["cn003", "cn004", "cn005"],
        ["cn006"],
    ]


def test_shard_slices_rejects_zero_size() -> None:
    with pytest.raises(ValueError):
        shard_slices(["a"], 0)


# ── streamed fingerprint parity ─────────────────────────────────────────


async def _seed_build_inputs(db_session, run_id: str) -> None:
    db_session.add(RunRecord(
        run_id=run_id, control_number="990000000000000002",
        marc={"_control_number": "990000000000000002", "dates": {"year": 1300}},
    ))
    db_session.add(AuthorityMatch(
        run_id=run_id, control_number="990000000000000002",
        entity_text="Rashi", entity_kind="person", role="author",
        viaf_id="8975316", wikidata_qid="Q42439",
        confidence="high", source="cross_source",
        payload={"birth_year": 1040}, approved=True,
    ))
    db_session.add(ExtractionApproval(
        run_id=run_id, control_number="990000000000000002",
        text="Rashi", type="PERSON", source="person_ner",
        start=0, end=5, approved=True,
    ))
    db_session.add(WikidataItemOverride(
        run_id=run_id, local_id="person::viaf:8975316",
        labels={"en": "Rashi (override)"}, approved=True,
    ))
    await db_session.commit()


async def test_streamed_fingerprint_matches_sequential(db_session, sample_run) -> None:
    """The sharded orchestrator's cache check must agree with the
    sequential build's — otherwise a Modal build would invalidate (or
    worse, reuse) a cache row the web path just served."""
    run_id = sample_run["run_id"]
    await _seed_build_inputs(db_session, run_id)

    records = (await db_session.execute(
        select(RunRecord).where(RunRecord.run_id == run_id))).scalars().all()
    matches = (await db_session.execute(
        select(AuthorityMatch).where(AuthorityMatch.run_id == run_id))).scalars().all()
    entities = (await db_session.execute(
        select(ExtractionApproval).where(ExtractionApproval.run_id == run_id))).scalars().all()
    overrides = (await db_session.execute(
        select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id))).scalars().all()

    sequential = compute_build_fingerprint(
        list(records), list(matches), list(entities), list(overrides), True,
    )
    streamed = await compute_build_fingerprint_streamed(
        db_session, run_id, approved_only=True,
    )
    assert streamed == sequential


async def test_streamed_fingerprint_flips_on_approval_change(db_session, sample_run) -> None:
    run_id = sample_run["run_id"]
    await _seed_build_inputs(db_session, run_id)
    before = await compute_build_fingerprint_streamed(
        db_session, run_id, approved_only=True,
    )
    rows = (await db_session.execute(
        select(AuthorityMatch).where(
            AuthorityMatch.run_id == run_id,
            AuthorityMatch.entity_text == "Rashi",
        )
    )).scalars().all()
    rows[0].approved = False
    await db_session.commit()
    after = await compute_build_fingerprint_streamed(
        db_session, run_id, approved_only=True,
    )
    assert before != after


async def test_list_control_numbers_is_cn_ordered(db_session, sample_run) -> None:
    run_id = sample_run["run_id"]
    await _seed_build_inputs(db_session, run_id)
    cns = await list_run_control_numbers(db_session, run_id)
    assert cns == sorted(cns)
    assert "990000000000000002" in cns


# ── native item payload round-trip ──────────────────────────────────────


def _native_person() -> object:
    from converter.wikidata.item_models import WikidataItem, WikidataStatement

    return WikidataItem(
        labels={"en": "Rashi", "he": "רש״י"},
        descriptions={"en": "Medieval commentator"},
        aliases={"en": ["Solomon ben Isaac"]},
        statements=[
            WikidataStatement(
                property_id="P214", value="8975316", value_type="external-id",
                qualifiers=[{"property": "P813", "value": "2026-01-01"}],
                references=[{"P248": "Q54919"}],
            ),
        ],
        entity_type="person",
        local_id="viaf:8975316",
        records=["990000000000000001", "990000000000000002"],
        authority_evidence=[{"viaf": "8975316"}],
    )


def test_native_item_payload_round_trip() -> None:
    item = _native_person()
    payload = native_item_payload(item)

    # The shard transport is JSON — the payload must survive it.
    rebuilt = native_item_from_payload(json.loads(json.dumps(payload)))
    assert native_item_payload(rebuilt) == payload
    assert rebuilt.labels == item.labels
    assert rebuilt.statements[0].qualifiers == item.statements[0].qualifiers
    assert rebuilt.statements[0].references == item.statements[0].references


# ── shard merge semantics ───────────────────────────────────────────────


def _person(local_id: str, records: list[str]) -> object:
    from converter.wikidata.item_models import WikidataItem

    return WikidataItem(
        labels={"en": local_id}, entity_type="person", local_id=local_id,
        records=records,
    )


def _work(local_id: str, records: list[str]) -> object:
    from converter.wikidata.item_models import WikidataItem

    return WikidataItem(
        labels={"en": local_id}, entity_type="work", local_id=local_id,
        records=records,
    )


def _manuscript(cn: str) -> object:
    from converter.wikidata.item_models import WikidataItem

    return WikidataItem(
        labels={"en": cn}, entity_type="manuscript", local_id=f"ms::{cn}",
        records=[cn],
    )


def test_merge_shard_items_dedupes_persons_first_wins() -> None:
    first = _person("viaf:1", ["cn001"])
    second = _person("viaf:1", ["cn002"])
    second.labels = {"en": "SHOULD NOT WIN"}
    shards = [
        [first, _manuscript("cn001")],
        [second, _manuscript("cn002")],
    ]
    merged = merge_shard_items(shards)
    persons = [i for i in merged if i.entity_type == "person"]
    assert len(persons) == 1
    assert persons[0].records == ["cn001", "cn002"]
    assert persons[0].labels == {"en": "viaf:1"}  # first shard wins
    manuscripts = [i for i in merged if i.entity_type == "manuscript"]
    assert len(manuscripts) == 2  # manuscripts never dedupe


def test_merge_shard_items_orders_works_persons_manuscripts() -> None:
    merged = merge_shard_items([
        [_manuscript("cn001"), _person("viaf:1", ["cn001"]), _work("work:1", ["cn001"])],
    ])
    types = [i.entity_type for i in merged]
    assert types == ["work", "person", "manuscript"]


def test_merge_shard_items_keeps_distinct_persons() -> None:
    merged = merge_shard_items([
        [_person("viaf:1", ["cn001"]), _person("viaf:2", ["cn001"])],
    ])
    assert len(merged) == 2
