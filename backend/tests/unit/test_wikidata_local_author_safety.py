"""Fail-closed local author and metadata resolution."""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.pipeline.wikidata_local_refs import (
    ensure_sourced_authors_resolvable,
    resolve_local_references,
)
from converter.wikidata.item_models import WikidataItem, WikidataStatement


@pytest.mark.parametrize("nested", [False, True])
def test_unsourced_p50_with_unresolved_qualifier_blocks(nested: bool) -> None:
    qualifier = (
        {"property": "P3831", "datatype": "wikibase-item", "snaktype": "value",
         "datavalue": {"type": "wikibase-entityid", "value": {"id": "__LOCAL:missing"}}}
        if nested else {"property": "P3831", "value": "__LOCAL:missing", "type": "item"}
    )
    claim = WikidataStatement("P50", "Q42", "item", qualifiers=[qualifier])
    work = WikidataItem(local_id="work:1", entity_type="work", statements=[claim])
    original = deepcopy(work)

    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        resolve_local_references([work])
    assert work == original


@pytest.mark.parametrize("sourced", [False, True])
def test_p50_local_target_must_be_a_person(sourced: bool) -> None:
    source = [{"property": "P854", "value": "https://example.org", "type": "url"}]
    claim = WikidataStatement(
        "P50", "__LOCAL:work:target", "item",
        references=source if sourced else [], reference_blocks=[source] if sourced else [],
    )
    author_work = WikidataItem(local_id="work:1", entity_type="work", statements=[claim])
    wrong_target = WikidataItem(local_id="work:target", entity_type="work")
    original = deepcopy(author_work)

    with pytest.raises(ValueError, match="P50.*person"):
        resolve_local_references([author_work, wrong_target])
    assert author_work == original


def test_ambiguous_duplicate_local_author_targets_block() -> None:
    claim = WikidataStatement("P50", "__LOCAL:target", "item")
    work = WikidataItem(local_id="work:1", entity_type="work", statements=[claim])
    person = WikidataItem(local_id="target", entity_type="person")
    wrong = WikidataItem(local_id="target", entity_type="work")
    with pytest.raises(ValueError, match="P50.*person"):
        resolve_local_references([work, person, wrong])


def test_duplicate_person_targets_are_not_a_unique_author() -> None:
    claim = WikidataStatement("P50", "__LOCAL:target", "item")
    work = WikidataItem(local_id="work:1", entity_type="work", statements=[claim])
    first = WikidataItem(local_id="target", entity_type="person", existing_qid="Q42")
    second = WikidataItem(local_id="target", entity_type="person", existing_qid="Q43")
    with pytest.raises(ValueError, match="P50.*person"):
        resolve_local_references([work, first, second])


@pytest.mark.parametrize("entity_type", ["work", "person"])
def test_nested_local_author_value_fails_closed(entity_type: str) -> None:
    work = {"local_id": "work:1", "entity_type": "work", "statements": [
        {"property_id": "P50", "value": {"id": "__LOCAL:target"}},
    ]}
    target = {"local_id": "target", "entity_type": entity_type}
    with pytest.raises(ValueError, match="P50.*local"):
        ensure_sourced_authors_resolvable([work, target])


def test_missing_author_type_on_serialized_target_blocks() -> None:
    work = {"local_id": "work:1", "entity_type": "work", "statements": [
        {"property_id": "P50", "value": "__LOCAL:target", "qualifiers": []},
    ]}
    with pytest.raises(ValueError, match="P50.*person"):
        ensure_sourced_authors_resolvable([work, {"local_id": "target"}])


def test_serialized_person_target_is_accepted() -> None:
    work = {"local_id": "work:1", "entity_type": "work", "statements": [
        {"property_id": "P50", "value": "__LOCAL:target", "qualifiers": []},
    ]}
    person = {"local_id": "target", "entity_type": "person", "statements": []}
    ensure_sourced_authors_resolvable([work, person])


def test_known_person_target_remains_until_upload_resolution() -> None:
    from app.pipeline.wikidata_live_native_hygiene import sanitize_studio_items_for_live

    source = [{"property": "P854", "value": "https://example.org", "type": "url"}]
    claim = WikidataStatement(
        "P50", "__LOCAL:person:1", "item", references=source, reference_blocks=[source],
        qualifiers=[{"property": "P3831", "value": "__LOCAL:work:role", "type": "item"}],
    )
    work = WikidataItem(local_id="work:1", entity_type="work", statements=[claim])
    person = WikidataItem(local_id="person:1", entity_type="person", existing_qid="Q42")
    role = WikidataItem(local_id="work:role", entity_type="work")

    resolve_local_references([work, person, role])
    assert claim.value == "__LOCAL:person:1"
    assert claim.reference_blocks == [source]
    sanitize_studio_items_for_live([work, person, role])
    assert claim.value == "__LOCAL:person:1"
    assert claim.reference_blocks == [source]
    assert claim.qualifiers[0]["value"] == "__LOCAL:work:role"


def test_malformed_empty_local_author_target_blocks_without_sources() -> None:
    claim = WikidataStatement("P50", "__LOCAL:", "item")
    work = WikidataItem(local_id="work:1", entity_type="work", statements=[claim])
    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        resolve_local_references([work])
