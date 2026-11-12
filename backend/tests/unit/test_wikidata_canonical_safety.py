"""Regression tests for canonical source and author isolation."""

from __future__ import annotations

from copy import deepcopy

import pytest

import converter.wikidata.item_builder  # noqa: F401  (import order)
from app.pipeline.wikidata_canonical_enrichment import (
    dedupe_statements,
    merge_legacy_into_canonical,
    prepare_wikidata_upload_native_items,
    rollup_dropped_person_local_refs,
)
from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.work_author_claims import promote_safe_work_author_claims
from converter.wikidata.work_identity import work_identity_for_item


def _source(url: str) -> list[dict[str, object]]:
    return [
        {"property": "P3959", "value": "990001", "type": "external-id"},
        {"property": "P854", "value": url, "type": "url"},
    ]


def _claim(
    pid: str, value: str, kind: str, source: list[dict[str, object]], **kwargs: object,
) -> WikidataStatement:
    return WikidataStatement(
        pid, value, kind, references=deepcopy(source),
        reference_blocks=[deepcopy(source)], **kwargs,
    )


def test_dedupe_preserves_distinct_time_precisions_and_sources() -> None:
    year_source = _source("https://example.org/year")
    day_source = _source("https://example.org/day")
    year = _claim("P571", "+1800-01-01T00:00:00Z", "time", year_source, precision=9)
    day = _claim("P571", "+1800-01-01T00:00:00Z", "time", day_source, precision=11)

    claims = dedupe_statements([year, day])

    assert [(s.precision, s.reference_blocks) for s in claims] == [
        (9, [year_source]), (11, [day_source]),
    ]


def test_dedupe_unions_sources_when_time_precision_matches() -> None:
    first = _source("https://example.org/first")
    second = _source("https://example.org/second")
    year = _claim("P571", "+1800-01-01T00:00:00Z", "time", first, precision=9)
    duplicate = _claim("P571", "+1800-01-01T00:00:00Z", "time", second, precision=9)

    claims = dedupe_statements([year, duplicate])
    assert len(claims) == 1
    assert claims[0].reference_blocks == [first, second]
    assert year.reference_blocks == [first]
    assert duplicate.reference_blocks == [second]


def test_dedupe_does_not_merge_a_time_with_a_string() -> None:
    first = _source("https://example.org/first")
    second = _source("https://example.org/second")
    time = _claim("P571", "+1800-01-01T00:00:00Z", "time", first, precision=9)
    text = _claim("P571", "+1800-01-01T00:00:00Z", "string", second)

    claims = dedupe_statements([time, text])
    assert [(claim.value_type, claim.reference_blocks) for claim in claims] == [
        ("time", [first]), ("string", [second]),
    ]


def test_canonical_merge_keeps_different_time_precisions_separate() -> None:
    year_source = _source("https://example.org/year")
    day_source = _source("https://example.org/day")
    canonical = WikidataItem(
        entity_type="manuscript", local_id="ms", records=["990001"],
        statements=[_claim("P571", "+1800-01-01T00:00:00Z", "time", year_source, precision=9)],
    )
    legacy = WikidataItem(
        entity_type="manuscript", local_id="legacy", records=["990001"],
        statements=[_claim("P571", "+1800-01-01T00:00:00Z", "time", day_source, precision=11)],
    )

    claims = merge_legacy_into_canonical([canonical], [legacy])[0].statements
    assert [(s.precision, s.reference_blocks) for s in claims] == [
        (9, [year_source]), (11, [day_source]),
    ]


def test_dedupe_does_not_contaminate_shared_input_on_later_reuse() -> None:
    first = _source("https://example.org/shared")
    second = _source("https://example.org/second")
    third = _source("https://example.org/third")
    shared = _claim("P217", "MS 1", "string", first)
    snapshot = deepcopy(shared)

    left = dedupe_statements([shared, _claim("P217", "MS 1", "string", second)])[0]
    right = dedupe_statements([shared, _claim("P217", "MS 1", "string", third)])[0]

    assert left.reference_blocks == [first, second]
    assert right.reference_blocks == [first, third]
    assert shared == snapshot


def test_author_promotion_does_not_contaminate_shared_p50() -> None:
    first = _source("https://example.org/shared")
    second = _source("https://example.org/second")
    third = _source("https://example.org/third")
    person = WikidataItem(
        entity_type="person", local_id="person:1", existing_qid="Q42",
        labels={"en": "Alice"}, records=["990001"],
    )
    shared = _claim("P50", "Q42", "item", first)
    snapshot = deepcopy(shared)
    works = [
        WikidataItem(entity_type="work", local_id=local_id, records=["990001"], statements=[
            shared, _claim("P2093", "Alice", "string", source),
        ])
        for local_id, source in [("left", second), ("right", third)]
    ]

    assert promote_safe_work_author_claims([person, *works]) == 2
    assert works[0].statements[0].reference_blocks == [first, second]
    assert works[1].statements[0].reference_blocks == [first, third]
    assert shared == snapshot


@pytest.mark.parametrize("existing_qid", [None, "Q42"])
def test_new_author_link_retains_source_name_and_identity(existing_qid: str | None) -> None:
    source = _source("https://example.org/component")
    author = _claim("P2093", "Alice", "string", source, rank="preferred")
    person = WikidataItem(
        local_id="person:1", entity_type="person", existing_qid=existing_qid,
        labels={"en": "Alice"}, records=["990001"],
        statements=[WikidataStatement("P8189", "987007000000005171", "external-id")],
    )
    work = WikidataItem(
        local_id="work:1", entity_type="work", records=["990001"], statements=[author],
    )
    original = deepcopy(author)

    assert promote_safe_work_author_claims([person, work]) == 1
    promoted = work.statements[0]
    assert promoted.property_id == "P50"
    assert promoted.value == (existing_qid or "__LOCAL:person:1")
    assert promoted.qualifiers == [{"property": "P1932", "value": "Alice", "type": "string"}]
    assert promoted.rank == original.rank
    assert promoted.reference_blocks == original.reference_blocks
    assert promoted.references == original.references
    assert work_identity_for_item(work).author_names == frozenset({"alice"})
    assert not work_identity_for_item(work).unresolved_authors
    assert author == original


def test_sourced_dropped_author_requires_verified_identity_even_with_a_name() -> None:
    source = _source("https://example.org/source")
    qualifier = {"property": "P958", "value": "1r", "type": "string"}
    claim = _claim("P50", "__LOCAL:person:1", "item", source, qualifiers=[qualifier])
    work = WikidataItem(entity_type="work", local_id="work:1", statements=[claim])
    snapshot = deepcopy(work)

    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        rollup_dropped_person_local_refs(
            [work], {"person:1"}, names_by_local_id={"person:1": "Alice"},
        )
    assert work == snapshot


def test_qualified_dropped_author_without_a_source_still_blocks() -> None:
    qualifier = {"property": "P958", "value": "1r", "type": "string"}
    claim = WikidataStatement("P50", "__LOCAL:person:1", "item", qualifiers=[qualifier])
    work = WikidataItem(entity_type="work", local_id="work:1", statements=[claim])

    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        rollup_dropped_person_local_refs(
            [work], {"person:1"}, names_by_local_id={"person:1": "Alice"},
        )
    assert work.statements == [claim]
    assert claim.qualifiers == [qualifier]


def test_upload_preparation_does_not_attribute_sources_to_unverified_person_label() -> None:
    source = _source("https://example.org/source")
    qualifier = {"property": "P958", "value": "1r", "type": "string"}
    person = WikidataItem(entity_type="person", local_id="person:1", labels={"en": "Alice"})
    claim = _claim("P50", "__LOCAL:person:1", "item", source, qualifiers=[qualifier])
    work = WikidataItem(entity_type="work", local_id="work:1", statements=[claim])
    snapshot = deepcopy(work)

    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        prepare_wikidata_upload_native_items([person, work])
    assert work == snapshot
