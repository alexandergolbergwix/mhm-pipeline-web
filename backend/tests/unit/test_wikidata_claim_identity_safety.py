"""Claim identity and sourced-author reference safety."""

from __future__ import annotations

from copy import deepcopy

import pytest

import converter.wikidata.item_builder  # noqa: F401  (import order)
from app.pipeline.wikidata_canonical_enrichment import (
    dedupe_statements,
    merge_legacy_into_canonical,
)
from app.pipeline.wikidata_local_refs import resolve_local_references
from converter.wikidata.item_models import WikidataItem, WikidataStatement


def _claim(
    pid: str, value: str | int, kind: str, url: str, **kwargs: object,
) -> WikidataStatement:
    source = [{"property": "P854", "value": url, "type": "url"}]
    return WikidataStatement(
        pid, value, kind, references=source, reference_blocks=[source], **kwargs,
    )


def _manuscript(statement: WikidataStatement) -> WikidataItem:
    return WikidataItem(entity_type="manuscript", local_id="ms:1", records=["990001"],
                        statements=[statement])


@pytest.mark.parametrize("merge", [False, True])
def test_distinct_quantity_units_keep_sources_separate(merge: bool) -> None:
    mm = _claim("P2048", 20, "quantity", "https://example.org/mm", unit="mm")
    cm = _claim("P2048", 20, "quantity", "https://example.org/cm", unit="cm")
    claims = (merge_legacy_into_canonical([_manuscript(mm)], [_manuscript(cm)])[0].statements
              if merge else dedupe_statements([mm, cm]))
    assert [(s.unit, s.reference_blocks) for s in claims] == [
        ("mm", mm.reference_blocks), ("cm", cm.reference_blocks),
    ]


@pytest.mark.parametrize("merge", [False, True])
def test_distinct_monolingual_languages_keep_sources_separate(merge: bool) -> None:
    he = _claim("P1680", "Text", "monolingualtext", "https://example.org/he", language="he")
    en = _claim("P1680", "Text", "monolingualtext", "https://example.org/en", language="en")
    claims = (merge_legacy_into_canonical([_manuscript(he)], [_manuscript(en)])[0].statements
              if merge else dedupe_statements([he, en]))
    assert [(s.language, s.reference_blocks) for s in claims] == [
        ("he", he.reference_blocks), ("en", en.reference_blocks),
    ]


@pytest.mark.parametrize("unit", ["mm", "Q174789", "http://www.wikidata.org/entity/Q174789"])
@pytest.mark.parametrize("merge", [False, True])
def test_equivalent_quantity_units_merge_sources(unit: str, merge: bool) -> None:
    first = _claim("P2048", 20, "quantity", "https://example.org/one", unit="mm")
    second = _claim("P2048", 20, "quantity", "https://example.org/two", unit=unit)
    claims = (merge_legacy_into_canonical([_manuscript(first)], [_manuscript(second)])[0].statements
              if merge else dedupe_statements([first, second]))
    assert len(claims) == 1
    assert claims[0].reference_blocks == first.reference_blocks + second.reference_blocks


def test_same_language_merges_sources_but_string_ignores_language_metadata() -> None:
    he = _claim("P1680", "Text", "monolingualtext", "https://example.org/one", language="he")
    also_he = _claim("P1680", "Text", "monolingualtext", "https://example.org/two", language="HE")
    result = dedupe_statements([he, also_he])
    assert len(result) == 1
    assert result[0].reference_blocks == he.reference_blocks + also_he.reference_blocks
    left = _claim("P2093", "Name", "string", "https://example.org/one", language="he")
    right = _claim("P2093", "Name", "string", "https://example.org/two", language="en")
    assert len(dedupe_statements([left, right])) == 1


def test_irrelevant_unit_and_language_do_not_change_time_identity() -> None:
    first = _claim("P571", "+1800-01-01T00:00:00Z", "time", "https://example.org/one",
                   precision=9, language="he", unit="mm")
    second = _claim("P571", "+1800-01-01T00:00:00Z", "time", "https://example.org/two",
                    precision=9, language="en", unit="cm")
    claims = dedupe_statements([first, second])
    assert len(claims) == 1
    assert claims[0].reference_blocks == first.reference_blocks + second.reference_blocks


def test_distinct_datatypes_do_not_merge_sources() -> None:
    text = _claim("P3959", "990001", "string", "https://example.org/one")
    external_id = _claim("P3959", "990001", "external-id", "https://example.org/two")
    claims = dedupe_statements([text, external_id])
    assert [(s.value_type, s.reference_blocks) for s in claims] == [
        ("string", text.reference_blocks), ("external-id", external_id.reference_blocks),
    ]


def test_unsourced_catalog_string_twin_is_removed_without_moving_evidence() -> None:
    bare = WikidataStatement("P3959", "990001", "string")
    source = _claim("P3959", "990001", "external-id", "https://example.org/source")
    claims = dedupe_statements([bare, source])
    assert len(claims) == 1
    assert claims[0].value_type == "external-id"
    assert claims[0].reference_blocks == source.reference_blocks


def test_qualified_catalog_string_twin_is_not_removed() -> None:
    bare = WikidataStatement(
        "P3959", "990001", "string",
        qualifiers=[{"property": "P958", "value": "1r", "type": "string"}],
    )
    source = _claim("P3959", "990001", "external-id", "https://example.org/source")
    assert len(dedupe_statements([bare, source])) == 2


def test_unqualified_catalog_string_is_not_removed_for_a_qualified_identifier() -> None:
    bare = WikidataStatement("P3959", "990001", "string")
    source = _claim("P3959", "990001", "external-id", "https://example.org/source",
                    qualifiers=[{"property": "P958", "value": "1r", "type": "string"}])
    assert len(dedupe_statements([bare, source])) == 2


def test_unitless_and_named_unit_quantities_do_not_merge() -> None:
    unitless = _claim("P2048", 20, "quantity", "https://example.org/one")
    mm = _claim("P2048", 20, "quantity", "https://example.org/two", unit="mm")
    claims = dedupe_statements([unitless, mm])
    assert len(claims) == 2
    assert claims[0].reference_blocks == unitless.reference_blocks
    assert claims[1].reference_blocks == mm.reference_blocks


def test_equal_dimensionless_quantities_merge_sources() -> None:
    first = _claim("P1104", 20, "quantity", "https://example.org/one", unit="")
    second = _claim("P1104", "20.0", "quantity", "https://example.org/two", unit="1")
    claims = dedupe_statements([first, second])
    assert len(claims) == 1
    assert len(claims[0].reference_blocks) == 2
    assert first.reference_blocks[0] in claims[0].reference_blocks
    assert second.reference_blocks[0] in claims[0].reference_blocks


def test_quantity_values_do_not_lose_decimal_precision() -> None:
    first = _claim("P2048", "20.0000000000000001", "quantity", "https://example.org/one", unit="mm")
    second = _claim("P2048", "20", "quantity", "https://example.org/two", unit="mm")
    claims = dedupe_statements([first, second])
    assert len(claims) == 2
    assert [s.reference_blocks for s in claims] == [first.reference_blocks, second.reference_blocks]


def test_ambiguous_comma_quantity_does_not_merge_with_an_integer() -> None:
    first = _claim("P2048", "20,5", "quantity", "https://example.org/one", unit="mm")
    second = _claim("P2048", "205", "quantity", "https://example.org/two", unit="mm")
    assert len(dedupe_statements([first, second])) == 2


def test_malformed_quantity_does_not_match_an_internal_numeric_key() -> None:
    first = _claim("P2048", "number:0:205:0", "quantity", "https://example.org/one", unit="mm")
    second = _claim("P2048", "205", "quantity", "https://example.org/two", unit="mm")
    assert len(dedupe_statements([first, second])) == 2


def test_rank_and_qualifiers_remain_part_of_identity() -> None:
    first = _claim("P2048", 20, "quantity", "https://example.org/one", unit="mm")
    preferred = _claim("P2048", 20, "quantity", "https://example.org/two",
                       unit="mm", rank="preferred")
    qualified = _claim("P2048", 20, "quantity", "https://example.org/three", unit="mm",
                       qualifiers=[{"property": "P518", "value": "Q1", "type": "item"}])
    claims = dedupe_statements([first, preferred, qualified])
    assert len(claims) == 3
    assert [s.reference_blocks for s in claims] == [
        first.reference_blocks, preferred.reference_blocks, qualified.reference_blocks,
    ]


def test_non_wikidata_unit_uri_does_not_match_a_public_unit() -> None:
    public = _claim("P2048", 20, "quantity", "https://example.org/one", unit="mm")
    other = _claim("P2048", 20, "quantity", "https://example.org/two",
                   unit="https://other.example/entity/Q174789")
    assert len(dedupe_statements([public, other])) == 2


@pytest.mark.parametrize(
    "location",
    ["qualifier", "nested-qualifier", "reference", "empty-reference", "nested-reference"],
)
def test_sourced_p50_with_unresolved_metadata_blocks_before_mutation(location: str) -> None:
    source = [{"property": "P854", "value": "https://example.org/source", "type": "url"}]
    qualifier: list[dict[str, object]] = []
    if location == "qualifier":
        qualifier = [{"property": "P3831", "value": "__LOCAL:missing", "type": "item"}]
    elif location == "nested-qualifier":
        qualifier = [{
            "property": "P3831", "datatype": "wikibase-item", "snaktype": "value",
            "datavalue": {"type": "wikibase-entityid", "value": {"id": "__LOCAL:missing"}},
        }]
    elif location == "reference":
        source.append({"property": "P248", "value": "__LOCAL:missing", "type": "item"})
    elif location == "empty-reference":
        source.append({"property": "P248", "value": "__LOCAL:", "type": "item"})
    else:
        source.append({
            "property": "P248", "datatype": "wikibase-item", "snaktype": "value",
            "datavalue": {"type": "wikibase-entityid", "value": {"id": "__LOCAL:missing"}},
        })
    claim = WikidataStatement("P50", "Q42", "item", qualifiers=qualifier,
                              references=source, reference_blocks=[source])
    work = WikidataItem(entity_type="work", local_id="work:1", statements=[claim])
    snapshot = deepcopy(work)
    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        resolve_local_references([work])
    assert work == snapshot


def test_sourced_author_preflight_runs_before_other_claims_are_dropped() -> None:
    earlier = WikidataStatement("P50", "__LOCAL:other", "item")
    first = WikidataItem(entity_type="work", local_id="work:first", statements=[earlier])
    source = [{"property": "P248", "value": "__LOCAL:missing", "type": "item"}]
    author = WikidataStatement("P50", "Q42", "item", references=source, reference_blocks=[source])
    second = WikidataItem(entity_type="work", local_id="work:second", statements=[author])
    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        resolve_local_references([first, second])
    assert first.statements == [earlier]
    assert second.statements == [author]


def test_known_local_p50_metadata_is_preserved() -> None:
    source = [{"property": "P248", "value": "__LOCAL:person:source", "type": "item"}]
    qualifier = [{"property": "P3831", "value": "__LOCAL:person:source", "type": "item"}]
    claim = WikidataStatement("P50", "Q42", "item", qualifiers=qualifier,
                              references=source, reference_blocks=[source])
    work = WikidataItem(entity_type="work", local_id="work:1", statements=[claim])
    source_item = WikidataItem(entity_type="person", local_id="person:source")
    resolve_local_references([work, source_item])
    assert work.statements[0].qualifiers == qualifier
    assert work.statements[0].reference_blocks == [source]


def test_sourced_author_metadata_preflight_supports_serialized_items() -> None:
    from app.pipeline.wikidata_local_refs import ensure_sourced_authors_resolvable

    item = {"local_id": "work:1", "statements": [{
        "property": "P50", "value": "Q42",
        "references": [{"property": "P248", "value": "__LOCAL:missing", "type": "item"}],
    }]}
    snapshot = deepcopy(item)
    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        ensure_sourced_authors_resolvable([item])
    assert item == snapshot
