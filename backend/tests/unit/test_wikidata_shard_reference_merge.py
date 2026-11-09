"""Shard merges must retain source groups without conflating claims or identities."""

from copy import deepcopy

import pytest

from app.pipeline.wikidata_studio import merge_shard_items
from converter.wikidata.item_models import WikidataItem, WikidataStatement


def _work(record: str, *, author: str = "Q42") -> WikidataItem:
    source = [{"property": "P854", "value": f"https://example.org/{record}", "type": "url"}]
    return WikidataItem(
        local_id="work:shared", entity_type="work", records=[record],
        labels={"en": "Shared title"},
        statements=[
            WikidataStatement("P1476", "Shared title", "monolingualtext", language="en",
                              references=deepcopy(source), reference_blocks=[deepcopy(source)]),
            WikidataStatement("P50", author, "item", references=deepcopy(source),
                              reference_blocks=[deepcopy(source)]),
        ],
        work_candidate_evidence=[{"source_record": record, "author_name": "Author"}],
    )


def test_shards_merge_complete_reference_blocks_without_mutating_sources() -> None:
    first, second = _work("1"), _work("2")
    originals = deepcopy([first, second])
    merged = merge_shard_items([[first], [second]])
    assert len(merged) == 1
    assert merged[0].records == ["1", "2"]
    for statement in merged[0].statements:
        assert [[snak["value"] for snak in block] for block in statement.reference_blocks] == [
            ["https://example.org/1"], ["https://example.org/2"],
        ]
    assert merged[0].work_candidate_evidence == (
        originals[0].work_candidate_evidence + originals[1].work_candidate_evidence
    )
    assert [first, second] == originals


def test_shards_match_unordered_qualifiers_and_deduplicate_source_blocks() -> None:
    first, second, third = _work("1"), _work("2"), _work("3")
    qualifiers = [{"property": "P1", "value": "a"}, {"property": "P2", "value": "b"}]
    for item in (first, second, third):
        item.statements[0].qualifiers = deepcopy(qualifiers)
    second.statements[0].qualifiers.reverse()
    second.statements.reverse()
    for statement, original in zip(third.statements, first.statements, strict=True):
        statement.references = deepcopy(original.references)
        statement.reference_blocks = deepcopy(original.reference_blocks)
    merged = merge_shard_items([[first], [second], [third]])
    assert merged[0].records == ["1", "2", "3"]
    assert all(len(statement.reference_blocks) == 2 for statement in merged[0].statements)


@pytest.mark.parametrize("change", [
    "author", "qualifier", "rank", "title", "missing_author", "unknown_author", "local_name",
    "different_qid", "extra_claim", "author_evidence", "source_author_evidence",
])
@pytest.mark.parametrize("reverse", [False, True])
def test_shards_reject_conflicting_or_ambiguous_work_identity(change: str, reverse: bool) -> None:
    first, second = _work("1"), _work("2")
    if change == "author":
        second.statements[1].value = "Q99"
    elif change == "qualifier":
        second.statements[1].qualifiers = [{"property": "P1932", "value": "Other"}]
    elif change == "rank":
        second.statements[1].rank = "preferred"
    elif change == "title":
        second.statements[0].value = "Other"
    elif change == "missing_author":
        first.statements.pop()
        second.statements.pop()
    elif change == "unknown_author":
        for item in (first, second):
            item.statements[1].property_id = "P2093"
            item.statements[1].value = "unknown"
            item.statements[1].value_type = "string"
    elif change == "local_name":
        first.statements[1].value = "__LOCAL:name:shared"
        second.statements[1].value = "__LOCAL:name:shared"
    elif change == "different_qid":
        first.existing_qid, second.existing_qid = "Q1", "Q2"
    elif change == "author_evidence":
        second.work_candidate_evidence[0]["author_name"] = "Another author"
    elif change == "source_author_evidence":
        for item, name in ((first, "Author"), (second, "Another author")):
            item.work_candidate_evidence = [{
                "source_field": "100/245", "source_text": f"Title \u2014 {name}",
            }]
    else:
        second.statements.append(WikidataStatement("P921", "Q5", "item"))
    originals = deepcopy([first, second])
    with pytest.raises(ValueError):
        merge_shard_items([[second], [first]] if reverse else [[first], [second]])
    assert [first, second] == originals


def test_shards_reject_ambiguous_legacy_flat_sources() -> None:
    first, second = _work("1"), _work("2")
    first.statements[0].reference_blocks = []
    first.statements[0].references.append({"property": "P248", "value": "Q1", "type": "item"})
    with pytest.raises(ValueError, match="Ambiguous flat references"):
        merge_shard_items([[first], [second]])


def test_override_addition_rejects_inconsistent_references() -> None:
    from app.pipeline.wikidata_studio import _dict_to_stmt

    old = {"property": "P854", "value": "https://example.org/old", "type": "url"}
    new = {"property": "P854", "value": "https://example.org/new", "type": "url"}
    with pytest.raises(ValueError, match="references do not match reference_blocks"):
        _dict_to_stmt({
            "property_id": "P407", "value": "Q1", "value_type": "item",
            "references": [old], "reference_blocks": [[new]],
        })


@pytest.mark.parametrize("reverse", [False, True])
def test_shards_add_source_backed_identifier_for_the_same_person(reverse: bool) -> None:
    source = [{"property": "P854", "value": "https://example.org/authority", "type": "url"}]
    first = WikidataItem(
        local_id="mazal:9870123", entity_type="person", records=["1"],
        labels={"en": "Person"}, statements=[WikidataStatement("P31", "Q5", "item")],
    )
    second = deepcopy(first)
    second.records = ["2"]
    second.labels["he"] = "Person"
    second.statements.append(WikidataStatement(
        "P214", "12345", "external-id", references=source, reference_blocks=[source],
    ))
    original = deepcopy([first, second])
    merged = merge_shard_items([[second], [first]] if reverse else [[first], [second]])
    person = merged[0]
    assert person.records == ["1", "2"]
    assert person.labels == {"en": "Person", "he": "Person"}
    viaf = next(statement for statement in person.statements if statement.property_id == "P214")
    assert viaf.value == "12345" and viaf.reference_blocks == [source]
    assert [first, second] == original
    from app.pipeline.wikidata_studio import native_item_from_payload, native_item_payload

    restored = native_item_from_payload(native_item_payload(person))
    assert restored.statements == person.statements


@pytest.mark.parametrize("entity_type,property_id", [("person", "P106"), ("work", "P407")])
def test_shards_add_source_backed_nonidentity_claim(entity_type: str, property_id: str) -> None:
    source = [{"property": "P854", "value": "https://example.org/source", "type": "url"}]
    if entity_type == "work":
        first, second = _work("1"), _work("2")
    else:
        first = WikidataItem(
            local_id="viaf:123", entity_type="person", records=["1"],
            labels={"en": "Person"}, statements=[WikidataStatement("P31", "Q5", "item")],
        )
        second = deepcopy(first)
        second.records = ["2"]
    second.statements.append(WikidataStatement(
        property_id, "Q42", "item", references=source, reference_blocks=[source],
    ))
    merged = merge_shard_items([[first], [second]])
    statement = next(s for s in merged[0].statements if s.property_id == property_id)
    assert statement.value == "Q42" and statement.reference_blocks == [source]


def test_shards_add_source_backed_identifier_to_same_work() -> None:
    first, second = _work("1"), _work("2")
    source = [{"property": "P854", "value": "https://example.org/work", "type": "url"}]
    second.statements.append(WikidataStatement(
        "P214", "12345", "external-id", references=source, reference_blocks=[source],
    ))
    merged = merge_shard_items([[first], [second]])
    identifier = next(s for s in merged[0].statements if s.property_id == "P214")
    assert identifier.value == "12345" and identifier.reference_blocks == [source]


@pytest.mark.parametrize("case", [
    "different_identifier", "local_identifier_conflict", "name_collision", "label_conflict",
    "unsupported_addition", "unsourced_identifier", "different_rank", "contradictory_instance",
    "bad_nli_identifier", "untrusted_evidence", "rejected_authority", "no_common_label",
])
@pytest.mark.parametrize("reverse", [False, True])
def test_shards_reject_unsafe_person_additions(case: str, reverse: bool) -> None:
    first = WikidataItem(
        local_id="viaf:123", entity_type="person", records=["1"], labels={"en": "Alice"},
        statements=[WikidataStatement("P31", "Q5", "item")],
    )
    second = deepcopy(first)
    second.records = ["2"]
    source = [{"property": "P854", "value": "https://example.org/source", "type": "url"}]
    added = WikidataStatement("P214", "123", "external-id", references=source,
                              reference_blocks=[source])
    if case == "different_identifier":
        first.statements.append(WikidataStatement("P214", "999", "external-id"))
    elif case == "local_identifier_conflict":
        added.value = "999"
    elif case == "name_collision":
        first.local_id = second.local_id = "name:alice"
        first.records = second.records = ["1"]
    elif case == "label_conflict":
        second.labels["en"] = "Bob"
    elif case == "unsupported_addition":
        added.property_id = "P50"
    elif case == "unsourced_identifier":
        added.references, added.reference_blocks = [], []
    elif case == "different_rank":
        first.statements.append(deepcopy(added))
        added.rank = "preferred"
    elif case == "bad_nli_identifier":
        added.property_id, added.value = "P8189", "99000123"
    elif case == "untrusted_evidence":
        second.authority_evidence = [{"rejection_reason": "untrusted heading"}]
    elif case == "rejected_authority":
        second.authority_evidence = [{"accepted": False}]
    elif case == "no_common_label":
        second.labels = {"he": "Bob"}
    else:
        second.statements[0].value = "Q43229"
    second.statements.append(added)
    original = deepcopy([first, second])
    with pytest.raises(ValueError):
        merge_shard_items([[second], [first]] if reverse else [[first], [second]])
    assert [first, second] == original


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("case", [
    "labels", "rejected", "guard_flags", "heading_mismatch", "name_collision",
])
def test_shards_always_reject_person_identity_conflicts(case: str, reverse: bool) -> None:
    first = WikidataItem(
        local_id="viaf:123", entity_type="person", records=["1"], labels={"en": "Alice"},
        statements=[WikidataStatement("P31", "Q5", "item")],
    )
    second = deepcopy(first)
    second.records = ["2"]
    if case == "labels":
        second.labels["en"] = "Bob"
    elif case == "rejected":
        second.authority_evidence = [{"accepted": False}]
    elif case == "guard_flags":
        second.authority_evidence = [{"guard_flags": ["identity_conflict"]}]
    elif case == "heading_mismatch":
        first.heading_mismatch = second.heading_mismatch = {"reason": "mismatch"}
    else:
        first.local_id = second.local_id = "name:alice"
        first.records = second.records = ["1"]
    originals = deepcopy([first, second])
    with pytest.raises(ValueError):
        merge_shard_items([[second], [first]] if reverse else [[first], [second]])
    assert [first, second] == originals


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("entity_type,property_id", [
    ("person", "P106"), ("work", "P407"), ("work", "P136"),
])
@pytest.mark.parametrize("values", [("Q1", "Q2"), ("Q1", "Q1")])
def test_shards_preserve_distinct_multivalue_claims(
    reverse: bool, entity_type: str, property_id: str, values: tuple[str, str],
) -> None:
    if entity_type == "work":
        first, second = _work("1"), _work("2")
    else:
        first = WikidataItem(
            local_id="viaf:123", entity_type="person", records=["1"],
            labels={"en": "Person"}, statements=[WikidataStatement("P31", "Q5", "item")],
        )
        second = deepcopy(first)
        second.records = ["2"]
    for index, item in enumerate((first, second)):
        source = [{"property": "P854", "value": f"https://example.org/{index}", "type": "url"}]
        item.statements.append(WikidataStatement(
            property_id, values[index], "item", references=source, reference_blocks=[source],
            qualifiers=[{"property": "P1932", "value": f"source {index}", "type": "string"}],
        ))
    merged = merge_shard_items([[second], [first]] if reverse else [[first], [second]])
    claims = [s for s in merged[0].statements if s.property_id == property_id]
    assert len(claims) == 2
    assert {claim.value for claim in claims} == set(values)
    assert {claim.qualifiers[0]["value"] for claim in claims} == {"source 0", "source 1"}
    assert {claim.reference_blocks[0][0]["value"] for claim in claims} == {
        "https://example.org/0", "https://example.org/1",
    }


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("entity_type,property_id", [
    ("person", "P106"), ("work", "P407"), ("work", "P136"),
])
def test_shards_merge_multivalue_subsets(reverse: bool, entity_type: str, property_id: str) -> None:
    if entity_type == "work":
        first, second = _work("1"), _work("2")
    else:
        first = WikidataItem(
            local_id="viaf:123", entity_type="person", records=["1"],
            labels={"en": "Person"}, statements=[WikidataStatement("P31", "Q5", "item")],
        )
        second = deepcopy(first)
        second.records = ["2"]
    for item, values, tag in ((first, ("Q1",), "first"), (second, ("Q1", "Q2"), "second")):
        for value in values:
            source = [{
                "property": "P854", "value": f"https://example.org/{tag}/{value}", "type": "url",
            }]
            item.statements.append(WikidataStatement(
                property_id, value, "item", references=source, reference_blocks=[source],
            ))
    merged = merge_shard_items([[second], [first]] if reverse else [[first], [second]])
    claims = {s.value: s for s in merged[0].statements if s.property_id == property_id}
    assert set(claims) == {"Q1", "Q2"}
    assert {block[0]["value"] for block in claims["Q1"].reference_blocks} == {
        "https://example.org/first/Q1", "https://example.org/second/Q1",
    }
    assert len(claims["Q2"].reference_blocks) == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_shards_reject_conflicting_ranks_for_same_multivalue(reverse: bool) -> None:
    first, second = _work("1"), _work("2")
    for item in (first, second):
        source = [{"property": "P854", "value": "https://example.org/source", "type": "url"}]
        item.statements.append(WikidataStatement(
            "P407", "Q1", "item", references=source, reference_blocks=[source],
        ))
    second.statements[-1].rank = "preferred"
    with pytest.raises(ValueError):
        merge_shard_items([[second], [first]] if reverse else [[first], [second]])


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("other_has_value", [False, True])
def test_shards_reject_conflicting_ranks_within_one_multivalue_source(
    reverse: bool, other_has_value: bool,
) -> None:
    first, second = _work("1"), _work("2")
    source = [{"property": "P854", "value": "https://example.org/source", "type": "url"}]
    first.statements.extend([
        WikidataStatement("P407", "Q1", "item", references=source, reference_blocks=[source]),
        WikidataStatement("P407", "Q1", "item", rank="preferred",
                          references=source, reference_blocks=[source]),
    ])
    if other_has_value:
        second.statements.append(WikidataStatement(
            "P407", "Q2", "item", references=source, reference_blocks=[source],
        ))
    with pytest.raises(ValueError):
        merge_shard_items([[second], [first]] if reverse else [[first], [second]])


def test_shards_reject_additional_work_author() -> None:
    first, second = _work("1"), _work("2")
    second.statements.append(WikidataStatement("P2093", "Another", "string"))
    with pytest.raises(ValueError):
        merge_shard_items([[first], [second]])


def test_shards_reject_different_record_scoped_works() -> None:
    first, second = _work("1"), _work("2")
    first.work_candidate_evidence[0]["identity_scope"] = "record"
    with pytest.raises(ValueError, match="record"):
        merge_shard_items([[first], [second]])


@pytest.mark.asyncio
async def test_shard_builder_merges_a_shared_source_backed_work() -> None:
    from app.pipeline.wikidata_studio import build_shard_items, native_item_from_payload
    from tests.unit.test_wikidata_studio_works import _fake_marc_record

    shards = []
    for control_number in ("990000000000000001", "990000000000000002"):
        record = {
            **_fake_marc_record(control_number),
            "contents": [{
                "title": "\u05d7\u05d6\u05d5\u05e7 \u05d0\u05de\u05d5\u05e0\u05d4",
                "author": "\u05d9\u05e6\u05d7\u05e7 \u05d1\u05df \u05d0\u05d1\u05e8\u05d4\u05dd",
                "source_field": "505", "candidate_kind": "named_work",
            }],
        }
        payload = await build_shard_items(
            marc_records=[record], approved_matches=[], entities_by_cn={},
        )
        shards.append([native_item_from_payload(item) for item in payload])
    works = [item for item in merge_shard_items(shards) if item.entity_type == "work"]
    assert len(works) == 1
    assert works[0].records == ["990000000000000001", "990000000000000002"]
    assert any(len(statement.reference_blocks) == 2 for statement in works[0].statements)


@pytest.mark.asyncio
async def test_shard_build_cache_fingerprint_changes_for_additive_merge(
    sample_run, db_session, monkeypatch,
) -> None:
    from app.pipeline import wikidata_studio_batches

    current = await wikidata_studio_batches.compute_build_fingerprint_streamed(
        db_session, sample_run["run_id"], approved_only=True,
    )
    monkeypatch.setattr(
        wikidata_studio_batches, "WIKIDATA_STUDIO_BUILD_SCHEMA", "source-aware-works-v17",
    )
    old = await wikidata_studio_batches.compute_build_fingerprint_streamed(
        db_session, sample_run["run_id"], approved_only=True,
    )
    assert current != old
