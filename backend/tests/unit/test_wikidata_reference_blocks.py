"""Source-block preservation in native Wikidata statement merges."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest
from wikibaseintegrator.datatypes import URL, ExternalID, Item

import converter.wikidata.item_builder  # noqa: F401  (import order)
from app.pipeline.wikidata_canonical_enrichment import (
    _rewrite_local_reference_aliases,
    dedupe_statements,
    merge_legacy_into_canonical,
    normalize_work_author_claims,
    rollup_dropped_person_local_refs,
)
from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.work_author_claims import promote_safe_work_author_claims


def _source(url: str, record: str) -> list[dict[str, str]]:
    return [
        {"property": "P854", "value": url, "type": "url"},
        {"property": "P3959", "value": record, "type": "external-id"},
    ]


def _statement(source: list[dict[str, str]], **kwargs: object) -> WikidataStatement:
    return WikidataStatement("P217", "MS 1", "string", references=source, **kwargs)


def _manuscript(statement: WikidataStatement) -> WikidataItem:
    return WikidataItem(
        local_id="ms:1", entity_type="manuscript", records=["123"],
        statements=[statement],
    )


def test_canonical_merge_preserves_complete_source_blocks_and_flat_projection() -> None:
    first = _source("https://example.org/one", "1")
    second = _source("https://example.org/two", "2")
    qualifiers = [{"property": "P580", "value": "2020", "type": "time"}]
    canonical = _statement(first, reference_blocks=[first], qualifiers=qualifiers, rank="preferred")
    legacy = _statement(
        second, reference_blocks=[second], qualifiers=list(qualifiers), rank="preferred",
    )

    merged = merge_legacy_into_canonical([_manuscript(canonical)], [_manuscript(legacy)])
    claim = merged[0].statements[0]

    assert claim.reference_blocks == [first, second]
    assert claim.references == first + second
    assert claim.qualifiers == qualifiers
    assert claim.rank == "preferred"


def test_duplicate_block_ignores_snak_and_dictionary_order() -> None:
    source = _source("https://example.org/one", "1")
    first = _statement(source, reference_blocks=[source])
    duplicate = _statement(source, reference_blocks=[
        [dict(reversed(list(snak.items()))) for snak in reversed(source)],
    ])
    claim = dedupe_statements([first, duplicate])[0]

    assert claim.reference_blocks == [source]
    assert claim.references == source


def test_duplicate_snaks_do_not_create_a_distinct_source_block() -> None:
    source = _source("https://example.org/one", "1")
    duplicate = source + [source[0]]
    claim = dedupe_statements([
        _statement(source, reference_blocks=[source]),
        _statement(duplicate, reference_blocks=[duplicate]),
    ])[0]
    assert len(claim.reference_blocks) == 1


def test_old_ambiguous_flat_references_are_not_promoted_to_a_block() -> None:
    old_flat = _source("https://example.org/old", "1") + _source("https://example.org/other", "2")
    new = _source("https://example.org/new", "3")
    with pytest.raises(ValueError, match="Ambiguous flat references"):
        dedupe_statements([_statement(old_flat), _statement(new, reference_blocks=[new])])


def test_explicit_blocks_take_precedence_over_flat_projection() -> None:
    first = _source("https://example.org/one", "1")
    second = _source("https://example.org/two", "2")
    third = _source("https://example.org/three", "3")
    claim = dedupe_statements([
        _statement(first + second, reference_blocks=[first, second]),
        _statement(third, reference_blocks=[third]),
    ])[0]

    assert claim.reference_blocks == [first, second, third]
    assert claim.references == first + second + third


def test_different_source_metadata_keeps_distinct_blocks() -> None:
    source = [{"property": "P813", "value": "2026-01-01", "type": "time", "precision": 11}]
    changed = [{**source[0], "precision": 9}]
    claim = dedupe_statements([
        _statement(source, reference_blocks=[source]),
        _statement(changed, reference_blocks=[changed]),
    ])[0]

    assert claim.reference_blocks == [source, changed]


def test_empty_explicit_block_is_not_silently_discarded() -> None:
    source = _source("https://example.org/one", "1")
    claim = dedupe_statements([
        _statement(source, reference_blocks=[[], source]),
        _statement(source, reference_blocks=[source]),
    ])[0]

    assert claim.reference_blocks == [[], source]


def test_unqualified_source_does_not_attach_to_qualified_claim() -> None:
    first = _source("https://example.org/one", "1")
    second = _source("https://example.org/two", "2")
    canonical = WikidataStatement("P361", "Q1", "item", references=first)
    legacy = WikidataStatement(
        "P361", "Q1", "item", references=second,
        qualifiers=[{"property": "P3831", "value": "Q2", "type": "item"}],
    )
    merged = merge_legacy_into_canonical([_manuscript(canonical)], [_manuscript(legacy)])
    claims = merged[0].statements
    assert len(claims) == 2
    assert claims[0].references == first and not claims[0].qualifiers
    assert claims[1].references == second and claims[1].qualifiers == legacy.qualifiers


def test_author_normalization_retains_qualifiers_and_source_blocks() -> None:
    first = _source("https://example.org/one", "1")
    second = _source("https://example.org/two", "1")
    qualifier = {"property": "P1932", "value": "Name", "type": "string"}
    person = WikidataItem(
        entity_type="person", local_id="person:1", existing_qid="Q123",
        labels={"en": "Name"}, records=["1"],
    )
    p50 = WikidataStatement(
        "P50", "Q123", "item", references=first, reference_blocks=[first], qualifiers=[qualifier],
    )
    p2093 = WikidataStatement(
        "P2093", "Name", "string", references=second,
        reference_blocks=[second], qualifiers=[qualifier],
    )
    work = WikidataItem(entity_type="work", records=["1"], statements=[p50, p2093])

    assert normalize_work_author_claims([person, work]) == 1
    assert len(work.statements) == 1
    assert work.statements[0].reference_blocks == [first, second]
    assert work.statements[0].qualifiers == [qualifier]
    assert p50.reference_blocks == [first]


def test_author_promotion_merges_sources_into_existing_p50() -> None:
    first = _source("https://example.org/one", "1")
    second = _source("https://example.org/two", "1")
    qualifier = {"property": "P1932", "value": "Name", "type": "string"}
    person = WikidataItem(
        entity_type="person", local_id="person:1", existing_qid="Q123",
        labels={"en": "Name"}, records=["1"],
    )
    p50 = WikidataStatement(
        "P50", "Q123", "item", references=first, reference_blocks=[first], qualifiers=[qualifier],
    )
    p2093 = WikidataStatement(
        "P2093", "Name", "string", references=second,
        reference_blocks=[second], qualifiers=[qualifier],
    )
    work = WikidataItem(entity_type="work", records=["1"], statements=[p50, p2093])

    assert promote_safe_work_author_claims([person, work]) == 1
    assert len(work.statements) == 1
    assert work.statements[0].reference_blocks == [first, second]
    assert work.statements[0].qualifiers == [qualifier]
    assert p50.reference_blocks == [first]


def test_author_promotion_merges_duplicate_names_after_new_p50() -> None:
    first = _source("https://example.org/one", "1")
    second = _source("https://example.org/two", "1")
    person = WikidataItem(
        entity_type="person", local_id="person:1", existing_qid="Q123",
        labels={"en": "Name"}, records=["1"],
    )
    work = WikidataItem(entity_type="work", records=["1"], statements=[
        WikidataStatement("P2093", "Name", "string", references=first, reference_blocks=[first]),
        WikidataStatement("P2093", "Name", "string", references=second, reference_blocks=[second]),
    ])

    assert promote_safe_work_author_claims([person, work]) == 2
    assert work.statements[0].reference_blocks == [first, second]


def test_author_rollup_blocks_unverified_qualifiers_rank_and_blocks() -> None:
    source = _source("https://example.org/one", "1")
    qualifier = {"property": "P1932", "value": "Name", "type": "string"}
    claim = WikidataStatement(
        "P50", "__LOCAL:person:1", "item", qualifiers=[qualifier],
        references=source, reference_blocks=[source], rank="preferred",
    )
    work = WikidataItem(entity_type="work", statements=[claim])

    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        rollup_dropped_person_local_refs(
            [work], {"person:1"}, names_by_local_id={"person:1": "Name"},
        )
    assert work.statements == [claim]
    assert claim.qualifiers == [qualifier]
    assert claim.references == source
    assert claim.reference_blocks == [source]
    assert claim.rank == "preferred"


def test_unresolved_sourced_author_blocks_before_any_source_is_lost() -> None:
    from app.pipeline.wikidata_local_refs import resolve_local_references

    source = _source("https://example.org/one", "1")
    qualifier = {"property": "P958", "value": "1r", "type": "string"}
    unresolved = WikidataStatement(
        "P50", "__LOCAL:person:missing", "item", qualifiers=[qualifier],
        references=source, reference_blocks=[source],
    )
    other_author = WikidataStatement(
        "P2093", "Unverified author", "string", references=_source("https://example.org/two", "1"),
    )
    work = WikidataItem(
        entity_type="work", local_id="work:1", statements=[unresolved, other_author],
    )
    assert normalize_work_author_claims([work]) == 0

    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        resolve_local_references([work])

    assert work.statements == [unresolved, other_author]
    assert unresolved.reference_blocks == [source]
    assert unresolved.qualifiers == [qualifier]
    assert other_author.references == _source("https://example.org/two", "1")


def test_unresolved_sourced_author_blocks_before_other_items_change() -> None:
    from app.pipeline.wikidata_local_refs import resolve_local_references

    earlier = WikidataStatement("P50", "__LOCAL:person:other", "item")
    earlier_work = WikidataItem(entity_type="work", local_id="work:earlier", statements=[earlier])
    source = _source("https://example.org/one", "1")
    claim = WikidataStatement("P50", "__LOCAL:person:missing", "item", references=source)
    later_work = WikidataItem(entity_type="work", local_id="work:later", statements=[claim])
    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        resolve_local_references([earlier_work, later_work])
    assert earlier_work.statements == [earlier]
    assert later_work.statements == [claim]


def test_build_fails_closed_before_sourced_author_can_be_removed() -> None:
    from app.pipeline.wikidata_studio import finish_native_items

    source = _source("https://example.org/one", "1")
    claim = WikidataStatement(
        "P50", "__LOCAL:person:missing", "item", references=source, reference_blocks=[source],
    )
    work = WikidataItem(entity_type="work", local_id="work:1", statements=[claim])
    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        finish_native_items([work])
    assert work.statements == [claim]
    assert claim.reference_blocks == [source]


def test_qualified_unresolved_author_blocks_without_a_reference() -> None:
    from app.pipeline.wikidata_local_refs import resolve_local_references

    qualifier = {"property": "P958", "value": "1r", "type": "string"}
    claim = WikidataStatement("P50", "__LOCAL:person:missing", "item", qualifiers=[qualifier])
    work = WikidataItem(entity_type="work", local_id="work:1", statements=[claim])
    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        resolve_local_references([work])
    assert work.statements == [claim]
    assert claim.qualifiers == [qualifier]


@pytest.mark.parametrize("as_dict", [False, True])
def test_live_hygiene_blocks_unresolved_sourced_author(as_dict: bool) -> None:
    from app.pipeline.wikidata_live_native_hygiene import sanitize_studio_items_for_live

    source = _source("https://example.org/one", "1")
    claim = WikidataStatement(
        "P50", "__LOCAL:person:missing", "item", references=source, reference_blocks=[source],
    )
    work = WikidataItem(entity_type="work", local_id="work:1", statements=[claim])
    item = asdict(work) if as_dict else work
    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        sanitize_studio_items_for_live([item])
    statements = item["statements"] if isinstance(item, dict) else item.statements
    assert len(statements) == 1
    blocks = statements[0]["reference_blocks"] if as_dict else statements[0].reference_blocks
    assert blocks == [source]


def test_author_rollup_without_a_name_does_not_drop_source_evidence() -> None:
    source = _source("https://example.org/one", "1")
    claim = WikidataStatement(
        "P50", "__LOCAL:person:missing", "item", references=source, reference_blocks=[source],
    )
    work = WikidataItem(entity_type="work", local_id="work:1", statements=[claim])
    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        rollup_dropped_person_local_refs([work], {"person:missing"}, names_by_local_id={})
    assert work.statements == [claim]
    assert claim.reference_blocks == [source]


def test_alias_rewrite_reaches_reference_blocks() -> None:
    block = [{"property": "P248", "value": "__LOCAL:old", "type": "item"}]
    claim = WikidataStatement("P217", "MS 1", "string", references=block, reference_blocks=[block])
    item = WikidataItem(statements=[claim])

    assert _rewrite_local_reference_aliases([item], {"old": "new"}) >= 1
    assert claim.reference_blocks[0][0]["value"] == "__LOCAL:new"


def test_dataclass_json_round_trip_retains_blocks_and_metadata() -> None:
    first = _source("https://example.org/one", "1")
    second = [{"property": "P813", "value": "2026-01-01", "type": "time", "precision": 11}]
    statement = WikidataStatement(
        "P217", "MS 1", "string", references=first + second,
        reference_blocks=[first, second], rank="preferred",
        qualifiers=[{"property": "P580", "value": "2020", "type": "time"}],
    )
    restored = WikidataStatement(**json.loads(json.dumps(asdict(statement))))

    assert restored == statement
    assert WikidataStatement("P217", "MS 1", "string").reference_blocks == []


def test_native_shard_json_round_trip_retains_reference_blocks() -> None:
    from app.pipeline.wikidata_studio import native_item_from_payload, native_item_payload

    first = _source("https://example.org/one", "1")
    second = _source("https://example.org/two", "2")
    item = _manuscript(_statement(first + second, reference_blocks=[first, second]))

    payload = json.loads(json.dumps(native_item_payload(item)))
    restored = native_item_from_payload(payload)
    assert restored.statements[0].reference_blocks == [first, second]
    assert restored.statements[0].references == first + second


def test_native_shard_round_trip_retains_blocks_with_shared_snaks() -> None:
    from app.pipeline.wikidata_studio import native_item_from_payload, native_item_payload

    shared = {"property": "P248", "value": "Q1", "type": "item"}
    first = [shared, *_source("https://example.org/one", "1")]
    second = [shared, *_source("https://example.org/two", "2")]
    statement = dedupe_statements([
        _statement(first, reference_blocks=[first]),
        _statement(second, reference_blocks=[second]),
    ])[0]
    assert statement.references.count(shared) == 1

    payload = json.loads(json.dumps(native_item_payload(_manuscript(statement))))
    restored = native_item_from_payload(payload).statements[0]
    assert restored.reference_blocks == [first, second]


def test_wbi_reference_snaks_deduplicate_against_native_aliases() -> None:
    native = [
        {"property": "P248", "value": "Q42", "type": "item", "value_label": "source"},
        {"property": "P854", "value": "https://example.org", "type": "url"},
        {"property": "P3959", "value": "123", "value_type": "external-id"},
    ]
    wbi = [
        Item(prop_nr="P248", value="Q42").mainsnak.get_json(),
        URL(prop_nr="P854", value="https://example.org").mainsnak.get_json(),
        ExternalID(prop_nr="P3959", value="123").mainsnak.get_json(),
    ]
    claim = dedupe_statements([
        _statement(native, reference_blocks=[native]),
        _statement(wbi, reference_blocks=[wbi]),
    ])[0]

    assert claim.reference_blocks == [native]
    assert claim.references == native


def test_wbi_time_precision_keeps_distinct_sources() -> None:
    from wikibaseintegrator.datatypes import Time

    first = [Time(prop_nr="P813", time="+2026-01-01T00:00:00Z", precision=11)
             .mainsnak.get_json()]
    second = [Time(prop_nr="P813", time="+2026-01-01T00:00:00Z", precision=9)
              .mainsnak.get_json()]
    claim = dedupe_statements([
        _statement(first, reference_blocks=[first]),
        _statement(second, reference_blocks=[second]),
    ])[0]
    assert claim.reference_blocks == [first, second]


def test_native_and_wbi_time_snaks_share_semantics() -> None:
    from wikibaseintegrator.datatypes import Time

    native = [{
        "property": "P813", "value": "+2026-01-01T00:00:00Z",
        "value_type": "time", "precision": 11, "property_label": "retrieved",
    }]
    wbi = [Time(prop_nr="P813", time="+2026-01-01T00:00:00Z", precision=11)
           .mainsnak.get_json()]
    claim = dedupe_statements([
        _statement(native, reference_blocks=[native]),
        _statement(wbi, reference_blocks=[wbi]),
    ])[0]
    assert claim.reference_blocks == [native]


@pytest.mark.parametrize("flat", [[], _source("https://example.org/other", "2")])
def test_conflicting_flat_references_fail_closed(flat: list[dict[str, str]]) -> None:
    block = _source("https://example.org/one", "1")
    with pytest.raises(ValueError, match="references and reference_blocks disagree"):
        _statement(flat, reference_blocks=[block])


def test_mutated_conflicting_flat_references_fail_before_deduplication() -> None:
    source = _source("https://example.org/one", "1")
    claim = _statement(source, reference_blocks=[source])
    claim.references = _source("https://example.org/other", "2")
    with pytest.raises(ValueError, match="references and reference_blocks disagree"):
        dedupe_statements([claim])


def test_shard_reader_rejects_conflicting_flat_references() -> None:
    from app.pipeline.wikidata_studio import native_item_from_payload

    payload = {
        "entity_type": "work",
        "statements": [{
            "property_id": "P217", "value": "MS 1", "value_type": "string",
            "references": _source("https://example.org/one", "1"),
            "reference_blocks": [_source("https://example.org/two", "2")],
        }],
    }
    with pytest.raises(ValueError, match="references do not match reference_blocks"):
        native_item_from_payload(payload)


def test_conflicting_reference_type_aliases_fail_closed() -> None:
    source = [{"property": "P248", "value": "Q42", "type": "item", "value_type": "url"}]
    with pytest.raises(ValueError, match="Conflicting reference value types"):
        _statement(source, reference_blocks=[source])


def test_conflicting_wbi_item_ids_fail_closed() -> None:
    source = [Item(prop_nr="P248", value="Q42").mainsnak.get_json()]
    source[0]["datavalue"]["value"]["numeric-id"] = 99
    with pytest.raises(ValueError, match="Conflicting reference item identifiers"):
        _statement(source, reference_blocks=[source])


def test_conflicting_wbi_datavalue_type_fails_closed() -> None:
    source = [Item(prop_nr="P248", value="Q42").mainsnak.get_json()]
    source[0]["datavalue"]["type"] = "string"
    with pytest.raises(ValueError, match="Conflicting reference datavalue type"):
        _statement(source, reference_blocks=[source])


def test_shared_legacy_item_does_not_contaminate_other_canonical_items() -> None:
    legacy_source = _source("https://example.org/legacy", "1")
    left_source = _source("https://example.org/left", "1")
    right_source = _source("https://example.org/right", "1")
    legacy = WikidataItem(
        entity_type="work", local_id="legacy", existing_qid="Q42",
        statements=[_statement(legacy_source, reference_blocks=[legacy_source])],
    )
    left = WikidataItem(
        entity_type="work", local_id="left", existing_qid="Q42",
        statements=[_statement(left_source, reference_blocks=[left_source])],
    )
    right = WikidataItem(
        entity_type="work", local_id="right", existing_qid="Q42",
        statements=[_statement(right_source, reference_blocks=[right_source])],
    )
    merged = merge_legacy_into_canonical([left, right], [legacy])
    assert merged[0].statements[0].reference_blocks == [left_source, legacy_source]
    assert merged[1].statements[0].reference_blocks == [right_source, legacy_source]
    assert legacy.statements[0].reference_blocks == [legacy_source]
    assert left.statements[0].references == left_source
    assert right.statements[0].references == right_source


def test_unqualified_combined_inscription_keeps_its_own_source() -> None:
    first = _source("https://example.org/one", "1")
    second = _source("https://example.org/two", "2")
    canonical = WikidataStatement("P1684", "First | Second", "string", references=first)
    qualified = WikidataStatement(
        "P1684", "First", "string", references=second,
        qualifiers=[{"property": "P958", "value": "1r", "type": "string"}],
    )
    merged = merge_legacy_into_canonical([_manuscript(canonical)], [_manuscript(qualified)])
    claims = merged[0].statements
    assert len(claims) == 2
    assert claims[0].references == first and not claims[0].qualifiers
    assert claims[1].references == second and claims[1].qualifiers == qualified.qualifiers


def test_normalization_does_not_merge_unrelated_or_unverified_authors() -> None:
    person = WikidataItem(
        entity_type="person", local_id="p1", existing_qid="Q42",
        labels={"en": "Alice"}, records=["1"],
    )
    p50 = WikidataStatement("P50", "Q42", "item", references=_source("https://a", "1"))
    unrelated = WikidataStatement("P2093", "Bob", "string", references=_source("https://b", "1"))
    no_record_match = WikidataStatement(
        "P2093", "Alice", "string", references=_source("https://c", "2"),
    )
    work = WikidataItem(
        entity_type="work", records=["2"], statements=[p50, unrelated, no_record_match],
    )
    assert normalize_work_author_claims([person, work]) == 0
    assert work.statements == [p50, unrelated, no_record_match]
    assert p50.reference_blocks == []


def test_normalization_requires_every_source_to_match_the_person_record() -> None:
    person = WikidataItem(
        entity_type="person", local_id="p1", existing_qid="Q42",
        labels={"en": "Alice"}, records=["1"],
    )
    first = _source("https://example.org/one", "1")
    other = _source("https://example.org/two", "2")
    p50 = WikidataStatement("P50", "Q42", "item")
    name = WikidataStatement(
        "P2093", "Alice", "string", references=first + other,
        reference_blocks=[first, other],
    )
    work = WikidataItem(entity_type="work", records=["1", "2"], statements=[p50, name])
    assert normalize_work_author_claims([person, work]) == 0
    assert work.statements == [p50, name]
    assert not p50.references


def test_promotion_does_not_attach_a_different_record_to_a_person() -> None:
    person = WikidataItem(
        entity_type="person", local_id="p1", existing_qid="Q42",
        labels={"en": "Alice"}, records=["1"],
    )
    source = _source("https://example.org/two", "2")
    author = WikidataStatement("P2093", "Alice", "string", references=source)
    work = WikidataItem(entity_type="work", records=["1", "2"], statements=[author])
    assert promote_safe_work_author_claims([person, work]) == 0
    assert work.statements == [author]
    assert author.references == source


def test_normalization_does_not_merge_an_unsourced_author_name() -> None:
    person = WikidataItem(
        entity_type="person", local_id="p1", existing_qid="Q42",
        labels={"en": "Alice"}, records=["1"],
    )
    p50 = WikidataStatement("P50", "Q42", "item")
    name = WikidataStatement("P2093", "Alice", "string")
    work = WikidataItem(entity_type="work", records=["1"], statements=[p50, name])
    assert normalize_work_author_claims([person, work]) == 0
    assert work.statements == [p50, name]


def test_normalization_rejects_a_qid_shared_by_distinct_people() -> None:
    people = [
        WikidataItem(
            entity_type="person", local_id=local_id, existing_qid="Q42",
            labels={"en": name}, records=["1"],
        )
        for local_id, name in [("p1", "Alice"), ("p2", "Bob")]
    ]
    p50 = WikidataStatement("P50", "Q42", "item")
    name = WikidataStatement("P2093", "Alice", "string", references=_source("https://a", "1"))
    work = WikidataItem(entity_type="work", records=["1"], statements=[p50, name])
    assert normalize_work_author_claims([*people, work]) == 0
    assert work.statements == [p50, name]


def test_promotion_requires_unique_compatible_qualified_occurrence() -> None:
    person = WikidataItem(
        entity_type="person", local_id="p1", existing_qid="Q42",
        labels={"en": "Alice"}, records=["1"],
    )
    folio1 = {"property": "P958", "value": "1r", "type": "string"}
    folio2 = {"property": "P958", "value": "2r", "type": "string"}
    p50_a = WikidataStatement("P50", "Q42", "item", qualifiers=[folio1])
    p50_b = WikidataStatement("P50", "Q42", "item", qualifiers=[folio2])
    p2093 = WikidataStatement("P2093", "Alice", "string", references=_source("https://a", "1"))
    work = WikidataItem(entity_type="work", records=["1"], statements=[p50_a, p50_b, p2093])
    assert promote_safe_work_author_claims([person, work]) == 0
    assert work.statements == [p50_a, p50_b, p2093]
    assert not p50_a.references and not p50_b.references


def test_promotion_leaves_ambiguous_identical_p50_claims_separate() -> None:
    person = WikidataItem(
        entity_type="person", local_id="p1", existing_qid="Q42",
        labels={"en": "Alice"}, records=["1"],
    )
    first = WikidataStatement("P50", "Q42", "item")
    second = WikidataStatement("P50", "Q42", "item")
    name = WikidataStatement("P2093", "Alice", "string")
    work = WikidataItem(entity_type="work", records=["1"], statements=[first, second, name])
    assert promote_safe_work_author_claims([person, work]) == 0
    assert work.statements == [first, second, name]
