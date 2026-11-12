"""Wikibase reference blocks at the upload boundary."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from wikibaseintegrator import WikibaseIntegrator

from app.pipeline.wikidata_deferred_payload import build_deferred_link_payload
from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.test_wiki_compat import (
    WikiTestAdaptStats,
    choose_test_property,
    filter_item_for_test_wiki,
    mark_fresh_live_reference,
    pid_map_key,
    rewrite_item_with_maps,
)
from converter.wikidata.uploader import (
    ClaimBuildError,
    WikidataUploader,
    partition_unresolved_local,
    resolve_local_statement_refs,
    resolve_statement_locals,
)


def _uploader(*, is_test: bool = False) -> WikidataUploader:
    return WikidataUploader(token="Fixture@Bot:not-a-credential", is_test=is_test, allow_live=not is_test)


def _source(url: str) -> list[dict[str, object]]:
    return [{"property": "P854", "value": url, "type": "url"}]


def _register_target(uploader: WikidataUploader, local_id: str, qid: str) -> None:
    item = WikidataItem(entity_type="person", local_id=local_id)
    assert uploader.register_test_ledger_target(item, {f"wikidata-test:person:{local_id}": qid}) == qid


def _statement(pid: str, value: str, kind: str, blocks: list[list[dict[str, object]]]) -> WikidataStatement:
    return WikidataStatement(pid, value, kind, references=copy.deepcopy([snak for block in blocks for snak in block]),
                             reference_blocks=copy.deepcopy(blocks))


def _item_with_existing_claim(monkeypatch: pytest.MonkeyPatch, uploader: WikidataUploader, claim: object) -> object:
    return _item_with_existing_claims(monkeypatch, uploader, [claim])


def _item_with_existing_claims(monkeypatch: pytest.MonkeyPatch, uploader: WikidataUploader, claims: list[object]) -> object:
    data = []
    for claim_index, claim in enumerate(claims, 1):
        entry = copy.deepcopy(claim.get_json())
        entry["id"] = f"Q123$00000000-0000-0000-0000-{claim_index:012d}"
        for index, reference in enumerate(entry.get("references", [])):
            reference["hash"] = f"{claim_index * 10 + index:040x}"
        data.append(entry)
    existing = WikibaseIntegrator().item.new().from_json({
        "id": "Q123", "type": "item", "lastrevid": 1,
        "labels": {}, "descriptions": {}, "aliases": {},
        "claims": {data[0]["mainsnak"]["property"]: data}, "sitelinks": {},
    })
    monkeypatch.setattr(uploader, "_init_wbi", lambda: SimpleNamespace(
        item=SimpleNamespace(get=lambda *args, **kwargs: existing),
    ))
    uploader._is_our_item_cache["Q123"] = True
    return existing


def _urls(claim: object) -> set[str]:
    return {
        reference["snaks"]["P854"][0]["datavalue"]["value"]
        for reference in claim.get_json()["references"]
    }


def test_deferred_pass_preserves_other_source_work_links(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader()
    old = _statement("P1574", "Q142031906", "item", [_source("https://example.org/fragment")])
    existing = _item_with_existing_claim(monkeypatch, uploader, uploader._build_claim(old))
    old_claim = copy.deepcopy(existing.get_json()["claims"]["P1574"][0])
    new = _statement("P1574", "Q141991984", "item", [_source("https://example.org/pancieri")])
    native = WikidataItem(
        local_id="ms:1", entity_type="manuscript", existing_qid="Q123",
        statements=[WikidataStatement("P31", "Q87167", "item"), old, new],
    )
    payload = build_deferred_link_payload(
        native, source_qid="Q123", resolved=[new], known_qids={}, is_test=False,
    )

    built, _, _ = uploader._build_wbi_item(payload, read_only=True)

    claims = built.get_json()["claims"]["P1574"]
    assert {c["mainsnak"]["datavalue"]["value"]["id"] for c in claims} == {
        "Q142031906", "Q141991984",
    }
    assert next(c for c in claims if c["id"] == old_claim["id"]) == old_claim


@pytest.mark.parametrize(("existing_rank", "proposed_rank"), [
    ("normal", "preferred"), ("preferred", "normal"),
])
def test_accepted_foreign_manuscript_preserves_existing_class_rank(
    monkeypatch: pytest.MonkeyPatch, existing_rank: str, proposed_rank: str,
) -> None:
    uploader = _uploader()
    old = _statement("P31", "Q123172729", "item", [_source("https://example.org/old")])
    old.rank = existing_rank
    existing = _item_with_existing_claim(monkeypatch, uploader, uploader._build_claim(old))
    before = copy.deepcopy(existing.get_json())
    uploader._is_our_item_cache["Q123"] = False
    uploader.register_foreign_accept("Q123")
    new = _statement("P31", "Q123172729", "item", [_source("https://example.org/new")])
    new.rank = proposed_rank

    result, _, _ = uploader._build_wbi_item(WikidataItem(
        entity_type="manuscript", existing_qid="Q123", statements=[new],
    ), read_only=True)

    claims = result.claims.get("P31")
    assert len(claims) == 1
    assert claims[0].get_json()["rank"] == existing_rank
    assert claims[0].get_json()["id"] == before["claims"]["P31"][0]["id"]
    assert _urls(claims[0]) == {"https://example.org/old", "https://example.org/new"}
    assert new.rank == proposed_rank


@pytest.mark.parametrize(("accepted", "owned", "entity_type", "old_rank", "new_rank"), [
    (False, False, "manuscript", "normal", "preferred"),
    (True, True, "manuscript", "normal", "preferred"),
    (True, False, "work", "normal", "preferred"),
    (True, False, "manuscript", "deprecated", "normal"),
    (True, False, "manuscript", "normal", "deprecated"),
])
def test_rank_preservation_does_not_relax_other_rank_conflicts(
    monkeypatch: pytest.MonkeyPatch, accepted: bool, owned: bool, entity_type: str,
    old_rank: str, new_rank: str,
) -> None:
    uploader = _uploader()
    old = _statement("P31", "Q123172729", "item", [_source("https://example.org/old")])
    old.rank = old_rank
    _item_with_existing_claim(monkeypatch, uploader, uploader._build_claim(old))
    uploader._is_our_item_cache["Q123"] = owned
    if accepted:
        uploader.register_foreign_accept("Q123")
    new = _statement("P31", "Q123172729", "item", [_source("https://example.org/new")])
    new.rank = new_rank
    with pytest.raises(ClaimBuildError, match="conflicting P31 rank"):
        uploader._build_wbi_item(WikidataItem(
            entity_type=entity_type, existing_qid="Q123", statements=[new],
        ), read_only=True)


def test_foreign_class_rank_requires_one_exact_existing_claim(monkeypatch) -> None:
    uploader = _uploader()
    normal = _statement("P31", "Q123172729", "item", [_source("https://example.org/one")])
    preferred = copy.deepcopy(normal)
    preferred.rank = "preferred"
    _item_with_existing_claims(monkeypatch, uploader, [
        uploader._build_claim(normal), uploader._build_claim(preferred),
    ])
    uploader._is_our_item_cache["Q123"] = False
    uploader.register_foreign_accept("Q123")
    with pytest.raises(ClaimBuildError, match="ambiguous matching P31"):
        uploader._build_wbi_item(WikidataItem(
            entity_type="manuscript", existing_qid="Q123", statements=[preferred],
        ), read_only=True)


def test_foreign_class_rank_does_not_cross_a_qualifier_boundary(monkeypatch) -> None:
    uploader = _uploader()
    old = _statement("P31", "Q123172729", "item", [_source("https://example.org/old")])
    old.qualifiers = [{"property": "P1480", "type": "item", "value": "Q18122778"}]
    _item_with_existing_claim(monkeypatch, uploader, uploader._build_claim(old))
    uploader._is_our_item_cache["Q123"] = False
    uploader.register_foreign_accept("Q123")
    new = _statement("P31", "Q123172729", "item", [_source("https://example.org/new")])
    new.rank = "preferred"
    result, _, _ = uploader._build_wbi_item(WikidataItem(
        entity_type="manuscript", existing_qid="Q123", statements=[new],
    ), read_only=True)
    claims = result.claims.get("P31")
    assert len(claims) == 2
    assert {c.get_json()["rank"] for c in claims} == {"normal", "preferred"}
    assert len([c for c in claims if c.get_json().get("qualifiers")]) == 1


def test_owned_link_update_retains_old_and_new_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader()
    old = _statement("P50", "Q55", "item", [_source("https://example.org/old")])
    _item_with_existing_claim(monkeypatch, uploader, uploader._build_claim(old))
    new = _statement("P50", "Q55", "item", [_source("https://example.org/new")])
    result, count, _ = uploader._build_wbi_item(WikidataItem(existing_qid="Q123", statements=[new]), read_only=True)
    assert count > 0
    assert _urls(result.claims.get("P50")[0]) == {"https://example.org/old", "https://example.org/new"}


def test_owned_replace_all_keeps_new_blocks_on_equal_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader()
    old = _statement("P571", "one", "string", [_source("https://example.org/old")])
    _item_with_existing_claim(monkeypatch, uploader, uploader._build_claim(old))
    item = WikidataItem(existing_qid="Q123", statements=[
        WikidataStatement("P31", "Q1", "item"),
        _statement("P571", "one", "string", [_source("https://example.org/new")]),
        _statement("P571", "two", "string", [_source("https://example.org/other")]),
    ])
    result, _, _ = uploader._build_wbi_item(item, read_only=True)
    claims = result.claims.get("P571")
    assert len(claims) == 2
    assert _urls(claims[0]) == {"https://example.org/old", "https://example.org/new"}
    assert _urls(claims[1]) == {"https://example.org/other"}


def test_owner_only_preserves_existing_references_and_adds_distinct_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader()
    old = _statement("P50", "Q55", "item", [_source("https://example.org/old")])
    _item_with_existing_claim(monkeypatch, uploader, uploader._build_claim(old, owner_only=True))
    new = _statement("P50", "Q55", "item", [
        _source("https://example.org/old"), _source("https://example.org/new"),
    ])
    item = WikidataItem(existing_qid="Q123", statements=[new])
    result, count, _ = uploader._build_wbi_item(item, read_only=True, owner_only=True)
    assert count == 1
    assert _urls(result.claims.get("P50")[0]) == {"https://example.org/old", "https://example.org/new"}


@pytest.mark.parametrize("mode", ["full", "link", "ordinary", "owner", "foreign"])
def test_references_merge_only_on_exact_rank_and_qualifiers(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    uploader = _uploader()
    pid = "P50" if mode in ("link", "owner") else "P571"
    kind, value = ("item", "Q55") if pid == "P50" else ("string", "same")
    original = []
    for rank, qualifier, source in [
        ("normal", "A", "normal"), ("preferred", "A", "preferred"), ("preferred", "B", "other"),
    ]:
        stmt = _statement(pid, value, kind, [_source(f"https://example.org/{source}")])
        stmt.rank = rank
        stmt.qualifiers = [{"property": "P1932", "value": qualifier, "type": "string"}]
        original.append(stmt)
    existing = _item_with_existing_claims(monkeypatch, uploader, [uploader._build_claim(stmt) for stmt in original])
    if mode == "foreign":
        uploader._is_our_item_cache["Q123"] = False
    target = copy.deepcopy(original[1])
    target.references = _source("https://example.org/new")
    target.reference_blocks = [copy.deepcopy(target.references)]
    proposed = [target]
    if mode == "full":
        proposed.insert(0, WikidataStatement("P31", "Q1", "item"))
    item = WikidataItem(existing_qid="Q123", statements=proposed)
    result, _, _ = uploader._build_wbi_item(item, read_only=True, owner_only=mode == "owner")
    claims = result.claims.get(pid)
    assert len(claims) == 3
    assert all(not claim.removed for claim in claims)
    assert [claim.rank.value for claim in claims] == ["normal", "preferred", "preferred"]
    assert [_urls(claim) for claim in claims] == [
        {"https://example.org/normal"},
        {"https://example.org/preferred", "https://example.org/new"},
        {"https://example.org/other"},
    ]
    assert result is existing


@pytest.mark.parametrize("mode", ["full", "link", "ordinary", "owner", "foreign"])
def test_duplicate_exact_claims_block_without_reference_mutation(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    uploader = _uploader()
    pid = "P50" if mode in ("link", "owner") else "P571"
    kind, value = ("item", "Q55") if pid == "P50" else ("string", "same")
    old = _statement(pid, value, kind, [_source("https://example.org/old")])
    existing = _item_with_existing_claims(monkeypatch, uploader, [uploader._build_claim(old), uploader._build_claim(old)])
    if mode == "foreign":
        uploader._is_our_item_cache["Q123"] = False
    proposed = _statement(pid, value, kind, [_source("https://example.org/new")])
    statements = [proposed]
    if mode == "full":
        statements.insert(0, WikidataStatement("P31", "Q1", "item"))
    with pytest.raises(ClaimBuildError, match="ambiguous"):
        uploader._build_wbi_item(WikidataItem(existing_qid="Q123", statements=statements), read_only=True,
                                 owner_only=mode == "owner")
    assert all(_urls(claim) == {"https://example.org/old"} for claim in existing.claims.get(pid))


@pytest.mark.parametrize("mode", ["full", "link", "ordinary", "owner", "foreign"])
def test_rank_conflict_blocks_instead_of_changing_existing_rank(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    uploader = _uploader()
    pid = "P50" if mode in ("link", "owner") else "P571"
    kind, value = ("item", "Q55") if pid == "P50" else ("string", "same")
    old = _statement(pid, value, kind, [_source("https://example.org/old")])
    existing = _item_with_existing_claims(monkeypatch, uploader, [uploader._build_claim(old)])
    if mode == "foreign":
        uploader._is_our_item_cache["Q123"] = False
    proposed = _statement(pid, value, kind, [_source("https://example.org/new")])
    proposed.rank = "preferred"
    statements = [proposed]
    if mode == "full":
        statements.insert(0, WikidataStatement("P31", "Q1", "item"))
    with pytest.raises(ClaimBuildError, match="rank"):
        uploader._build_wbi_item(WikidataItem(existing_qid="Q123", statements=statements), read_only=True,
                                 owner_only=mode == "owner")
    claim = existing.claims.get(pid)[0]
    assert claim.rank.value == "normal"
    assert _urls(claim) == {"https://example.org/old"}


def test_upload_rank_conflict_returns_blocked_without_write(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader()
    old = _statement("P50", "Q55", "item", [_source("https://example.org/old")])
    existing = _item_with_existing_claims(monkeypatch, uploader, [uploader._build_claim(old)])
    writes: list[bool] = []
    monkeypatch.setattr(existing, "write", lambda **kwargs: writes.append(True))
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None)
    proposed = _statement("P50", "Q55", "item", [_source("https://example.org/new")])
    proposed.rank = "preferred"
    result = uploader.upload_item(WikidataItem(existing_qid="Q123", statements=[proposed]))
    assert result.status == "blocked"
    assert "rank" in result.message
    assert writes == []
    assert existing.claims.get("P50")[0].rank.value == "normal"
    assert _urls(existing.claims.get("P50")[0]) == {"https://example.org/old"}


def test_upload_merges_references_into_exact_rank_before_write(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader()
    normal = _statement("P50", "Q55", "item", [_source("https://example.org/normal")])
    preferred = _statement("P50", "Q55", "item", [_source("https://example.org/preferred")])
    preferred.rank = "preferred"
    existing = _item_with_existing_claims(monkeypatch, uploader, [
        uploader._build_claim(normal), uploader._build_claim(preferred),
    ])
    writes: list[object] = []

    def write(**kwargs: object) -> object:
        writes.append(copy.deepcopy(existing.get_json()))
        return existing

    monkeypatch.setattr(existing, "write", write)
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(uploader, "_rate_limit", lambda: None)
    proposed = _statement("P50", "Q55", "item", [_source("https://example.org/new")])
    proposed.rank = "preferred"
    result = uploader.upload_item(WikidataItem(existing_qid="Q123", statements=[proposed]))
    assert result.status == "updated"
    assert len(writes) == 1
    claims = existing.claims.get("P50")
    assert [claim.rank.value for claim in claims] == ["normal", "preferred"]
    assert [_urls(claim) for claim in claims] == [
        {"https://example.org/normal"}, {"https://example.org/preferred", "https://example.org/new"},
    ]


def test_owner_only_full_update_accepts_complete_ranked_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader()
    normal = _statement("P50", "Q55", "item", [_source("https://example.org/normal")])
    preferred = _statement("P50", "Q55", "item", [_source("https://example.org/preferred")])
    preferred.rank = "preferred"
    _item_with_existing_claims(monkeypatch, uploader, [
        uploader._build_claim(normal, owner_only=True), uploader._build_claim(preferred, owner_only=True),
    ])
    new = copy.deepcopy(preferred)
    new.references = _source("https://example.org/new")
    new.reference_blocks = [copy.deepcopy(new.references)]
    item = WikidataItem(existing_qid="Q123", statements=[WikidataStatement("P31", "Q1", "item"), normal, new])
    updated, _, _ = uploader._build_wbi_item(item, read_only=True, owner_only=True)
    assert [_urls(claim) for claim in updated.claims.get("P50")] == [
        {"https://example.org/normal"}, {"https://example.org/preferred", "https://example.org/new"},
    ]


@pytest.mark.parametrize("full", [False, True])
def test_distinct_qualifiers_keep_separate_claims(monkeypatch: pytest.MonkeyPatch, full: bool) -> None:
    uploader = _uploader()
    old = _statement("P571", "same", "string", [_source("https://example.org/old")])
    old.qualifiers = [{"property": "P1932", "value": "A", "type": "string"}]
    _item_with_existing_claims(monkeypatch, uploader, [uploader._build_claim(old)])
    new = _statement("P571", "same", "string", [_source("https://example.org/new")])
    new.qualifiers = [{"property": "P1932", "value": "B", "type": "string"}]
    statements = [WikidataStatement("P31", "Q1", "item"), new] if full else [new]
    result, _, _ = uploader._build_wbi_item(WikidataItem(existing_qid="Q123", statements=statements), read_only=True)
    claims = result.claims.get("P571")
    assert len(claims) == 2
    assert all(not claim.removed for claim in claims)
    assert [_urls(claim) for claim in claims] == [{"https://example.org/old"}, {"https://example.org/new"}]


def test_owned_full_update_preserves_distinct_reference_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader()
    old = _statement("P571", "same", "string", [_source("https://example.org/old")])
    claim = uploader._build_claim(old)
    data = copy.deepcopy(claim.get_json())
    data["id"] = "Q123$00000000-0000-0000-0000-000000000001"
    reference = data["references"][0]
    reference["hash"] = "a" * 40
    second = copy.deepcopy(reference)
    second["hash"] = "b" * 40
    data["references"].append(second)
    existing = WikibaseIntegrator().item.new().from_json({
        "id": "Q123", "type": "item", "lastrevid": 1,
        "labels": {}, "descriptions": {}, "aliases": {}, "claims": {"P571": [data]}, "sitelinks": {},
    })
    monkeypatch.setattr(uploader, "_init_wbi", lambda: SimpleNamespace(item=SimpleNamespace(get=lambda *a, **k: existing)))
    uploader._is_our_item_cache["Q123"] = True
    new = _statement("P571", "same", "string", [_source("https://example.org/new")])
    item = WikidataItem(existing_qid="Q123", statements=[WikidataStatement("P31", "Q1", "item"), new])
    result, _, _ = uploader._build_wbi_item(item, read_only=True)
    references = list(result.claims.get("P571")[0].references)
    assert [ref.hash for ref in references] == ["a" * 40, "b" * 40, None]


def test_update_rejects_invalid_native_rank(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader()
    old = _statement("P571", "same", "string", [_source("https://example.org/old")])
    _item_with_existing_claims(monkeypatch, uploader, [uploader._build_claim(old)])
    old.rank = "invalid"
    with pytest.raises(ClaimBuildError, match="invalid rank"):
        uploader._build_wbi_item(WikidataItem(existing_qid="Q123", statements=[old]), read_only=True)


@pytest.mark.parametrize("owner_only", [False, True])
@pytest.mark.parametrize("changed_view", ["flat", "block"])
def test_direct_claim_rejects_reference_view_mismatch(owner_only: bool, changed_view: str) -> None:
    stmt = _statement("P571", "same", "string", [_source("https://example.org/old")])
    view = stmt.references if changed_view == "flat" else stmt.reference_blocks[0]
    view[0]["value"] = "https://example.org/other"
    with pytest.raises(ClaimBuildError, match="reference"):
        _uploader()._build_claim(stmt, owner_only=owner_only)


@pytest.mark.parametrize("owner_only", [False, True])
@pytest.mark.parametrize("use_blocks", [False, True])
@pytest.mark.parametrize("snak", [
    {"property": "P854", "value": "https://example.org", "type": "url", "unknown": "source evidence"},
    {"property": "P854", "value": "https://example.org", "type": "url", "hash": "a" * 40},
    {"property": "P854", "value": "https://example.org", "type": "url", "precision": 11},
    {"property": "P854", "value": "https://example.org", "type": ""},
    {"property": "P813", "value": "+2020-01-02T00:00:00Z", "type": "time", "uncertainty": 2},
    {"property": "P248", "value": {"id": "Q1", "unknown": "source evidence"}, "type": "item"},
    {"property": "P248", "value": {"id": "Q1", "numeric-id": 2}, "type": "item"},
    {"property": "P248", "value": {"id": "Q1", "entity-type": "property"}, "type": "item"},
])
def test_direct_claim_rejects_unrepresentable_snak_metadata(
    owner_only: bool, use_blocks: bool, snak: dict[str, object],
) -> None:
    stmt = WikidataStatement("P571", "same", "string")
    stmt.references = [copy.deepcopy(snak)]
    stmt.reference_blocks = [[copy.deepcopy(snak)]] if use_blocks else []
    with pytest.raises(ClaimBuildError, match="reference"):
        _uploader()._build_claim(stmt, owner_only=owner_only)


@pytest.mark.parametrize("owner_only", [False, True])
def test_direct_claim_checks_metadata_on_legacy_view_even_with_explicit_blocks(owner_only: bool) -> None:
    stmt = _statement("P571", "same", "string", [_source("https://example.org/old")])
    stmt.references[0]["hash"] = "a" * 40
    with pytest.raises(ClaimBuildError, match="reference"):
        _uploader()._build_claim(stmt, owner_only=owner_only)


@pytest.mark.parametrize("owner_only", [False, True])
def test_direct_claim_preserves_one_legacy_flat_snak(owner_only: bool) -> None:
    snaks = [
        {"property": "P854", "value": "https://example.org/old", "type": "url", "property_label": "source"},
    ]
    claim = _uploader()._build_claim(WikidataStatement("P571", "same", "string", references=snaks), owner_only=owner_only)
    references = claim.get_json()["references"]
    assert len(references) == 1
    assert set(references[0]["snaks"]) == {"P854"}


@pytest.mark.parametrize("owner_only", [False, True])
def test_upload_blocks_inconsistent_reference_views_before_local_resolution(
    monkeypatch: pytest.MonkeyPatch, owner_only: bool,
) -> None:
    uploader = _uploader()
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None)
    initialized: list[bool] = []
    monkeypatch.setattr(uploader, "_init_wbi", lambda: initialized.append(True))
    stmt = _statement("P571", "same", "string", [[
        {"property": "P248", "value": "__LOCAL:first", "type": "item"},
    ]])
    stmt.references[0]["value"] = "__LOCAL:second"
    item = WikidataItem(local_id="QDraft_MS_10001", entity_type="manuscript", existing_qid="Q123", records=["10001"],
                        statements=[WikidataStatement("P3959", "10001", "external-id"), stmt])
    result = uploader.upload_item(item, created_qids={"first": "Q1", "second": "Q1"},
                                  owner_only_qid="Q123" if owner_only else None)
    assert result.status == "blocked"
    assert "reference" in result.message
    assert stmt.references[0]["value"] == "__LOCAL:second"
    assert stmt.reference_blocks[0][0]["value"] == "__LOCAL:first"
    assert initialized == []


@pytest.mark.parametrize("owner_only", [False, True])
@pytest.mark.parametrize("use_blocks", [False, True])
def test_upload_blocks_unsupported_reference_metadata_before_write(
    monkeypatch: pytest.MonkeyPatch, owner_only: bool, use_blocks: bool,
) -> None:
    uploader = _uploader()
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None)
    initialized: list[bool] = []
    monkeypatch.setattr(uploader, "_init_wbi", lambda: initialized.append(True))
    stmt = WikidataStatement("P571", "same", "string", references=_source("https://example.org/old"))
    if use_blocks:
        stmt.reference_blocks = [copy.deepcopy(stmt.references)]
        stmt.reference_blocks[0][0]["unsupported"] = "source evidence"
    stmt.references[0]["unsupported"] = "source evidence"
    item = WikidataItem(local_id="QDraft_MS_10001", entity_type="manuscript", existing_qid="Q123", records=["10001"],
                        statements=[WikidataStatement("P3959", "10001", "external-id"), stmt])
    result = uploader.upload_item(item, owner_only_qid="Q123" if owner_only else None)
    assert result.status == "blocked"
    assert "unsupported reference metadata" in result.message
    assert initialized == []


@pytest.mark.parametrize("owner_only", [False, True])
def test_direct_claim_preserves_canonical_item_metadata(owner_only: bool) -> None:
    snak = {"property": "P248", "type": "item", "value": {"id": "Q123", "numeric-id": 123, "entity-type": "item"}}
    stmt = _statement("P571", "same", "string", [[snak]])
    claim = _uploader()._build_claim(stmt, owner_only=owner_only)
    value = claim.get_json()["references"][0]["snaks"]["P248"][0]["datavalue"]["value"]
    assert value == {"id": "Q123", "numeric-id": 123, "entity-type": "item"}


@pytest.mark.parametrize("raw", [None, {}, (), "", False])
def test_direct_claim_rejects_invalid_flat_reference_container(raw: object) -> None:
    stmt = WikidataStatement("P571", "same", "string")
    stmt.references = raw
    with pytest.raises(ClaimBuildError, match="Invalid references"):
        _uploader()._build_claim(stmt)


def test_test_wiki_prewarm_rejects_unknown_metadata_before_stub_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader(is_test=True)
    initialized: list[bool] = []
    monkeypatch.setattr(uploader, "_init_wbi", lambda: initialized.append(True))
    stmt = WikidataStatement("P571", "same", "string", references=_source("https://example.org/old"))
    stmt.references[0]["unsupported"] = "source evidence"
    item = WikidataItem(local_id="work", statements=[stmt])
    with pytest.raises(ClaimBuildError, match="unsupported reference metadata"):
        uploader._ensure_test_maps_for_item(item)
    uploader.warm_test_maps_for_items([item])
    assert initialized == []


@pytest.mark.parametrize("raw", [None, {}, (), "", False, 0])
def test_invalid_falsy_reference_blocks_refuse_build_and_test_remap(raw: object) -> None:
    stmt = WikidataStatement("P571", "value", "string")
    stmt.reference_blocks = raw
    item = WikidataItem(statements=[stmt])
    with pytest.raises(ClaimBuildError, match="Invalid reference blocks"):
        _uploader()._build_claim(stmt)
    mapped = rewrite_item_with_maps(item, {}, {})
    _, skipped = filter_item_for_test_wiki(mapped, property_datatypes={"P571": "string"}, existing_item_ids=set())
    assert "malformed reference blocks" in skipped
    _, adapted = _uploader(is_test=True)._adapt_item_for_test_wiki(item)
    assert "malformed reference blocks" in adapted.skipped


def test_local_resolution_updates_both_reference_views_and_defers() -> None:
    snak = {"property": "P248", "value": "__LOCAL:source", "type": "item"}
    stmt = WikidataStatement("P571", "x", "string", references=[dict(snak)], reference_blocks=[[dict(snak)]])
    item = WikidataItem(statements=[stmt])
    write, deferred = partition_unresolved_local(item, {})
    assert not write.statements
    rewritten, remaining = resolve_statement_locals(deferred[0], {"source": "Q900"})
    assert not remaining
    assert rewritten.references[0]["value"] == rewritten.reference_blocks[0][0]["value"] == "Q900"
    assert stmt.references[0]["value"] == stmt.reference_blocks[0][0]["value"] == "__LOCAL:source"


def test_test_wiki_preserves_local_namespace_even_on_numeric_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader(is_test=True)
    _register_target(uploader, "source", "Q900")
    refs = [
        {"property": "P248", "value": "__LOCAL:source", "type": "item"},
        {"property": "P50", "value": "http://www.wikidata.org/entity/Q900", "type": "item"},
    ]
    stmt = WikidataStatement("P571", "x", "string", references=copy.deepcopy(refs), reference_blocks=[refs])
    item = WikidataItem(statements=[stmt])
    assert not resolve_local_statement_refs(item, {"source": "Q900"})
    uploader._test_pid_map = {pid_map_key(p, t): p for p, t in [("P571", "string"), ("P248", "item"), ("P50", "item")]}
    uploader._test_qid_map = {"Q900": "Q999"}
    uploader._test_property_datatypes = {"P571": "string", "P248": "wikibase-item", "P50": "wikibase-item"}
    uploader._test_entity_exists = {"Q900": True, "Q999": True}
    monkeypatch.setattr(uploader, "_ensure_test_maps_for_item", lambda _, **_kwargs: WikiTestAdaptStats())
    adapted, result = uploader._adapt_item_for_test_wiki(item)
    assert not result.skipped
    for view in (adapted.statements[0].references, adapted.statements[0].reference_blocks[0]):
        assert [snak["value"] for snak in view] == ["Q900", "Q999"]
    claim = uploader._build_claim(adapted.statements[0])
    assert claim.get_json()["references"][0]["snaks"]["P248"][0]["datavalue"]["value"]["id"] == "Q900"


def test_test_wiki_rejects_unknown_live_qid_and_unknown_local_qid(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader(is_test=True)
    uploader._test_pid_map = {pid_map_key("P571", "string"): "P571", pid_map_key("P248", "item"): "P248"}
    uploader._test_property_datatypes = {"P571": "string", "P248": "wikibase-item"}
    uploader._test_entity_exists = {"Q900": True}
    monkeypatch.setattr(uploader, "_ensure_test_maps_for_item", lambda _, **_kwargs: WikiTestAdaptStats())
    monkeypatch.setattr(uploader, "_ensure_test_entity_exists_flags", lambda _: None)
    live = WikidataItem(statements=[_statement("P571", "x", "string", [[
        {"property": "P248", "value": "http://www.wikidata.org/entity/Q900", "type": "item"},
    ]])])
    _, result = uploader._adapt_item_for_test_wiki(live)
    assert any("no verified test item mapping" in reason for reason in result.skipped)
    local = copy.deepcopy(live)
    local.statements[0].references[0]["value"] = "__LOCAL:source"
    local.statements[0].reference_blocks[0][0]["value"] = "__LOCAL:source"
    _register_target(uploader, "source", "Q901")
    resolve_local_statement_refs(local, {"source": "Q901"})
    _, result = uploader._adapt_item_for_test_wiki(local)
    assert any("Q901" in reason and "missing" in reason for reason in result.skipped)


@pytest.mark.parametrize("created_qids", [None, {}, {"source": "Q900"}])
def test_test_upload_blocks_local_namespace_lost_in_json(
    monkeypatch: pytest.MonkeyPatch, created_qids: dict[str, str] | None,
) -> None:
    uploader = _uploader(is_test=True)
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None)
    initialized: list[bool] = []
    monkeypatch.setattr(uploader, "_init_wbi", lambda: initialized.append(True))
    uploader._test_qid_map = {"Q900": "Q999"}
    snak = {"property": "P248", "value": "__LOCAL:source", "type": "item"}
    stmt = _statement("P571", "x", "string", [[snak]])
    item = WikidataItem(local_id="work", statements=[stmt])
    resolve_local_statement_refs(item, {"source": "Q900"})
    restored = WikidataItem(local_id="work", statements=[WikidataStatement(**json.loads(json.dumps(asdict(stmt))))])
    outcome = uploader.upload_item(restored, created_qids=created_qids)
    assert outcome.status == "blocked"
    assert "test-ledger proof" in outcome.message
    assert initialized == []


def test_serialized_local_reference_with_explicit_live_collision_never_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploader = _uploader(is_test=True)
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None)
    writes: list[object] = []
    monkeypatch.setattr(uploader, "_init_wbi", lambda: writes.append("initialized"))
    uploader._test_qid_map = {"Q900": "Q999"}
    refs = [
        {"property": "P248", "value": "__LOCAL:source", "type": "item"},
        {"property": "P50", "value": "http://www.wikidata.org/entity/Q900", "type": "item"},
    ]
    item = WikidataItem(local_id="work", statements=[_statement("P571", "x", "string", [refs])])
    assert not resolve_local_statement_refs(item, {"source": "Q900"})
    payload = json.loads(json.dumps(asdict(item)))
    payload["statements"] = [WikidataStatement(**stmt) for stmt in payload["statements"]]
    restored = WikidataItem(**payload)
    result = uploader.upload_item(restored)
    assert result.status == "blocked"
    assert "test-ledger proof" in result.message
    assert writes == []
    remapped = rewrite_item_with_maps(restored, {pid_map_key("P248", "item"): "P248"}, {"Q900": "Q999"})
    assert remapped.statements[0].reference_blocks[0][0]["value"] == "Q900"
    _, skipped = filter_item_for_test_wiki(
        remapped, property_datatypes={"P571": "string", "P248": "wikibase-item", "P50": "wikibase-item"},
        existing_item_ids={"Q900", "Q999"},
    )
    assert any("ambiguous wiki namespace" in reason for reason in skipped)


def test_in_memory_local_and_explicit_live_collision_write_correct_qids(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader(is_test=True)
    _register_target(uploader, "source", "Q900")
    uploader._test_pid_map = {pid_map_key(p, t): p for p, t in [("P571", "string"), ("P248", "item"), ("P50", "item")]}
    uploader._test_qid_map = {"Q900": "Q999"}
    uploader._test_property_datatypes = {"P571": "string", "P248": "wikibase-item", "P50": "wikibase-item"}
    uploader._test_entity_exists = {"Q900": True, "Q999": True}
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(uploader, "_init_wbi", lambda: None)
    monkeypatch.setattr(uploader, "_ensure_test_maps_for_item", lambda _, **_kwargs: WikiTestAdaptStats())
    monkeypatch.setattr(uploader, "_rate_limit", lambda: None)
    monkeypatch.setattr(uploader, "_assert_modifiable", lambda *args, **kwargs: None)
    built: list[object] = []

    class FakeWbiItem:
        id = None

        def write(self, **kwargs: object) -> object:
            return SimpleNamespace(id="Q1000")

    def build(item: WikidataItem) -> tuple[object, int, list[str]]:
        built.append(uploader._build_claim(item.statements[0]).get_json())
        return FakeWbiItem(), 1, ["P571"]

    monkeypatch.setattr(uploader, "_build_wbi_item", build)
    refs = [
        {"property": "P248", "value": "__LOCAL:source", "type": "item"},
        {"property": "P50", "value": "http://www.wikidata.org/entity/Q900", "type": "item"},
    ]
    item = WikidataItem(local_id="work", statements=[_statement("P571", "x", "string", [refs])])
    result = uploader.upload_item(item, created_qids={"source": "Q900"})
    assert result.status == "success"
    snaks = built[0]["references"][0]["snaks"]
    assert snaks["P248"][0]["datavalue"]["value"]["id"] == "Q900"
    assert snaks["P50"][0]["datavalue"]["value"]["id"] == "Q999"


@pytest.mark.parametrize("representation", ["flat", "value", "datavalue"])
def test_test_wiki_reports_unsupported_reference_calendar_before_write(
    monkeypatch: pytest.MonkeyPatch, representation: str,
) -> None:
    uploader = _uploader(is_test=True)
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None)
    initialized: list[bool] = []
    monkeypatch.setattr(uploader, "_init_wbi", lambda: initialized.append(True))
    snak = {"property": "P813", "value": "+2020-01-02T00:00:00Z", "type": "time",
            "precision": 11, "calendarmodel": "http://www.wikidata.org/entity/Q1985786"}
    if representation != "flat":
        calendar = snak.pop("calendarmodel")
        value = {"time": snak.pop("value"), "calendarmodel": calendar, "precision": 11}
        if representation == "value":
            snak["value"] = value
        else:
            snak["datavalue"] = {"type": "time", "value": value}
    item = WikidataItem(local_id="work", statements=[_statement("P571", "x", "string", [[snak]])])
    _, adaptation = uploader._adapt_item_for_test_wiki(item)
    assert any("calendar remap is unsupported" in issue for issue in adaptation.skipped)
    _, skipped = filter_item_for_test_wiki(
        item, property_datatypes={"P571": "string", "P813": "time"}, existing_item_ids=set(),
    )
    assert any("calendar remap is unsupported" in issue for issue in skipped)
    result = uploader.upload_item(item)
    assert result.status == "blocked"
    assert "calendar remap is unsupported" in result.message
    assert initialized == []


@pytest.mark.parametrize("value", ["Q900", {"id": "Q900"}])
def test_ambiguous_reference_never_creates_test_mappings(
    monkeypatch: pytest.MonkeyPatch, value: object,
) -> None:
    uploader = _uploader(is_test=True)
    initialized: list[bool] = []
    monkeypatch.setattr(uploader, "_init_wbi", lambda: initialized.append(True))
    item = WikidataItem(local_id="work", statements=[_statement("P571", "x", "string", [[
        {"property": "P248", "value": value, "type": "item"},
    ]])])
    with pytest.raises(ClaimBuildError, match="ambiguous wiki namespace"):
        uploader._ensure_test_maps_for_item(item)
    uploader.warm_test_maps_for_items([item])
    assert initialized == []


def test_test_property_requires_matching_label_and_datatype() -> None:
    kwargs = dict(property_label="inventory number", property_datatypes={"P217": "string"}, pid_map={})
    assert choose_test_property("P217", "string", **kwargs, search_hits=[
        {"id": "P217", "label": "unrelated", "datatype": "string"},
    ]) is None
    assert choose_test_property("P217", "string", **kwargs, search_hits=[
        {"id": "P217", "label": "inventory number", "datatype": "string"},
    ]) == "P217"


def test_test_wiki_remaps_dictionary_item_values() -> None:
    snak = {"property": "P248", "value": {"id": "http://www.wikidata.org/entity/Q900"}, "datatype": "wikibase-item"}
    item = WikidataItem(statements=[_statement("P571", "x", "string", [[snak]])])
    mapped = rewrite_item_with_maps(item, {pid_map_key("P248", "wikibase-item"): "P2"}, {"Q900": "Q999"})
    block = mapped.statements[0].reference_blocks[0]
    assert block[0]["property"] == "P2"
    assert block[0]["value"] == "Q999"
    uploader = _uploader(is_test=True)
    uploader._test_qid_map = {"Q900": "Q999"}
    uploader._test_entity_exists = {"Q999": True}
    claim = uploader._build_claim(mapped.statements[0])
    assert claim.get_json()["references"][0]["snaks"]["P2"][0]["datavalue"]["value"]["id"] == "Q999"


def test_fresh_nli_reference_provenance_survives_json_and_remaps(monkeypatch: pytest.MonkeyPatch) -> None:
    from converter.wikidata.property_mapping import nli_reference

    source = nli_reference("990000000000000001")
    original = WikidataStatement("P571", "same", "string", references=source)
    stmt = mark_fresh_live_reference(original, source)
    restored = WikidataStatement(**json.loads(json.dumps(asdict(stmt))))
    assert restored.reference_blocks[0][0]["value"] == "Q118384267"
    assert restored.reference_blocks[0][0]["value_namespace"] == "wikidata"
    uploader = _uploader(is_test=True)
    uploader._test_qid_map = {"Q118384267": "Q900", "Q900": "Q901", "Q87167": "Q902"}
    uploader._test_pid_map = {pid_map_key(pid, typ): pid for pid, typ in [
        ("P31", "item"), ("P571", "string"), ("P248", "item"), ("P3959", "external-id"),
        ("P854", "url"), ("P813", "time"),
    ]}
    uploader._test_property_datatypes = {"P31": "wikibase-item", "P571": "string", "P248": "wikibase-item", "P3959": "external-id",
                                         "P854": "url", "P813": "time"}
    uploader._test_entity_exists = {"Q900": True, "Q901": True, "Q902": True}
    monkeypatch.setattr(uploader, "_ensure_test_maps_for_item", lambda _, **_kwargs: WikiTestAdaptStats())
    instance = WikidataStatement("P31", {"id": "Q87167", "value_namespace": "wikidata"}, "item")
    mapped, result = uploader._adapt_item_for_test_wiki(WikidataItem(statements=[instance, restored]))
    assert not result.skipped
    assert uploader._build_claim(mapped.statements[0]).get_json()["mainsnak"]["datavalue"]["value"]["id"] == "Q902"
    claim = uploader._build_claim(mapped.statements[1])
    reference = claim.get_json()["references"][0]["snaks"]
    assert reference["P248"][0]["datavalue"]["value"]["id"] == "Q900"
    assert set(reference) == {"P248", "P3959", "P854", "P813"}


def test_old_unmarked_nli_reference_is_blocked_after_json_round_trip() -> None:
    from converter.wikidata.property_mapping import nli_reference

    source = [dict(snak) for snak in nli_reference("990000000000000001")]
    source[0].pop("value_namespace")
    stmt = WikidataStatement("P571", "same", "string", references=source, reference_blocks=[source])
    item = WikidataItem(statements=[WikidataStatement(**json.loads(json.dumps(asdict(stmt))))])
    _, result = _uploader(is_test=True)._adapt_item_for_test_wiki(item)
    assert any("ambiguous wiki namespace" in reason for reason in result.skipped)


def test_fresh_source_provenance_refuses_a_mutated_or_unproven_source() -> None:
    from converter.wikidata.property_mapping import nli_reference

    source = nli_reference("990000000000000001")
    stmt = WikidataStatement("P571", "same", "string", references=source)
    with pytest.raises(ValueError, match="fresh source"):
        mark_fresh_live_reference(stmt, list(source))
    source[0]["value"] = "Q1"
    with pytest.raises(ValueError):
        mark_fresh_live_reference(stmt, source)


def test_local_test_reference_json_requires_exact_test_ledger_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader(is_test=True)
    source = WikidataItem(local_id="person:source", entity_type="person", existing_qid="Q900")
    live_ledger = {"wikidata:person:person:source": "Q900"}
    with pytest.raises(ValueError, match="test-ledger"):
        uploader.register_test_ledger_target(source, live_ledger)
    stmt = _statement("P571", "same", "string", [[
        {"property": "P248", "value": "__LOCAL:person:source", "type": "item"},
    ]])
    item = WikidataItem(statements=[stmt])
    resolve_local_statement_refs(item, {"person:source": "Q900"})
    restored = WikidataStatement(**json.loads(json.dumps(asdict(stmt))))
    assert restored.reference_blocks[0][0]["value_namespace"] == "local"
    assert restored.reference_blocks[0][0]["value_local_id"] == "person:source"
    _, result = uploader._adapt_item_for_test_wiki(WikidataItem(statements=[restored]))
    assert any("test-ledger proof" in reason for reason in result.skipped)
    with pytest.raises(ValueError, match="test-ledger"):
        uploader.register_test_ledger_target(source, {"wikidata-test:person:person:other": "Q900"})
    _register_target(uploader, "person:source", "Q900")
    uploader._test_pid_map = {pid_map_key("P571", "string"): "P571", pid_map_key("P248", "item"): "P248"}
    uploader._test_qid_map = {"Q900": "Q901"}
    uploader._test_property_datatypes = {"P571": "string", "P248": "wikibase-item"}
    uploader._test_entity_exists = {"Q900": True, "Q901": True}
    monkeypatch.setattr(uploader, "_ensure_test_maps_for_item", lambda _, **_kwargs: WikiTestAdaptStats())
    mapped, result = uploader._adapt_item_for_test_wiki(WikidataItem(statements=[restored]))
    assert not result.skipped
    claim = uploader._build_claim(mapped.statements[0])
    assert claim.get_json()["references"][0]["snaks"]["P248"][0]["datavalue"]["value"]["id"] == "Q900"


def test_test_upload_refuses_live_seed_without_test_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader(is_test=True)
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None)
    initialized: list[bool] = []
    monkeypatch.setattr(uploader, "_init_wbi", lambda: initialized.append(True))
    uploader._test_entity_exists = {"Q900": True}
    uploader._is_our_item_cache["Q900"] = True
    item = WikidataItem(local_id="work:one", statements=[WikidataStatement("P50", "__LOCAL:person:source", "item")])
    result = uploader.upload_item(item, created_qids={"person:source": "Q900"})
    assert result.status == "blocked"
    assert "__LOCAL:person:source" in result.message
    assert initialized == []


def test_test_upload_never_updates_same_number_without_exact_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader(is_test=True)
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None)
    initialized: list[bool] = []
    monkeypatch.setattr(uploader, "_init_wbi", lambda: initialized.append(True))
    uploader._is_our_item_cache["Q900"] = True
    item = WikidataItem(local_id="person:source", entity_type="person", existing_qid="Q900")
    result = uploader.upload_item(item)
    assert result.status == "blocked"
    assert "test-ledger proof" in result.message
    with pytest.raises(ClaimBuildError, match="test-ledger proof"):
        uploader._build_wbi_item(item)
    assert initialized == []


def test_successful_test_write_does_not_supply_ledger_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    from converter.wikidata.uploader import UploadResult

    uploader = _uploader(is_test=True)
    monkeypatch.setattr(uploader, "_upload_item", lambda item, **kwargs: UploadResult(
        local_id=item.local_id, qid="Q900", status="success",
    ))
    uploader.upload_item(WikidataItem(local_id="person:source"))
    assert uploader._verified_test_targets == {}


def test_pre_resolved_live_seed_is_refused_without_test_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader(is_test=True)
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None)
    initialized: list[bool] = []
    monkeypatch.setattr(uploader, "_init_wbi", lambda: initialized.append(True))
    item = WikidataItem(local_id="work:one", statements=[WikidataStatement("P50", "__LOCAL:person:source", "item")])
    resolve_local_statement_refs(item, {"person:source": "Q900"})
    result = uploader.upload_item(item, created_qids={"person:source": "Q900"})
    assert result.status == "blocked"
    assert "test-ledger proof" in result.message
    assert initialized == []


def test_explicit_live_main_qid_maps_despite_untrusted_same_number(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader(is_test=True)
    uploader._test_qid_map = {"Q900": "Q901"}
    uploader._test_pid_map = {pid_map_key("P31", "item"): "P31"}
    uploader._test_property_datatypes = {"P31": "wikibase-item"}
    uploader._test_entity_exists = {"Q901": True}
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(uploader, "_init_wbi", lambda: None)
    monkeypatch.setattr(uploader, "_ensure_test_maps_for_item", lambda _, **_kwargs: WikiTestAdaptStats())
    monkeypatch.setattr(uploader, "_rate_limit", lambda: None)
    monkeypatch.setattr(uploader, "_assert_modifiable", lambda *args, **kwargs: None)
    built: list[object] = []

    class FakeWbiItem:
        id = None

        def write(self, **kwargs: object) -> object:
            return SimpleNamespace(id="Q1000")

    def build(item: WikidataItem) -> tuple[object, int, list[str]]:
        built.append(uploader._build_claim(item.statements[0]).get_json())
        return FakeWbiItem(), 1, ["P31"]

    monkeypatch.setattr(uploader, "_build_wbi_item", build)
    item = WikidataItem(local_id="work", statements=[
        WikidataStatement("P31", {"id": "Q900", "value_namespace": "wikidata"}, "item"),
    ])
    result = uploader.upload_item(item, created_qids={"untrusted": "Q900"})
    assert result.status == "success"
    assert built[0]["mainsnak"]["datavalue"]["value"]["id"] == "Q901"


def test_test_local_mainsnak_provenance_survives_json_and_requires_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader(is_test=True)
    stmt = WikidataStatement("P50", "__LOCAL:person:source", "item")
    resolved, missing = resolve_statement_locals(stmt, {"person:source": "Q900"}, test_namespace=True)
    assert not missing
    restored = WikidataStatement(**json.loads(json.dumps(asdict(resolved))))
    assert restored.value == {"id": "Q900", "value_namespace": "local", "value_local_id": "person:source"}
    item = WikidataItem(statements=[restored])
    _, result = uploader._adapt_item_for_test_wiki(item)
    assert any("test-ledger proof" in reason for reason in result.skipped)
    _register_target(uploader, "person:source", "Q900")
    uploader._test_qid_map = {"Q900": "Q901"}
    uploader._test_pid_map = {pid_map_key("P50", "item"): "P50"}
    uploader._test_property_datatypes = {"P50": "wikibase-item"}
    uploader._test_entity_exists = {"Q900": True, "Q901": True}
    monkeypatch.setattr(uploader, "_ensure_test_maps_for_item", lambda _, **_kwargs: WikiTestAdaptStats())
    mapped, result = uploader._adapt_item_for_test_wiki(item)
    assert not result.skipped
    assert uploader._build_claim(mapped.statements[0]).get_json()["mainsnak"]["datavalue"]["value"]["id"] == "Q900"


def test_legacy_serialized_local_mainsnak_without_namespace_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader(is_test=True)
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None)
    initialized: list[bool] = []
    monkeypatch.setattr(uploader, "_init_wbi", lambda: initialized.append(True))
    stmt = WikidataStatement("P50", "__LOCAL:person:source", "item")
    resolved, missing = resolve_statement_locals(stmt, {"person:source": "Q900"})
    assert not missing
    restored = WikidataStatement(**json.loads(json.dumps(asdict(resolved))))
    assert restored.value == "Q900"
    uploader._test_qid_map = {"Q900": "Q901"}
    result = uploader.upload_item(WikidataItem(statements=[restored]))
    assert result.status == "blocked"
    assert "ambiguous wiki namespace" in result.message
    assert initialized == []


def test_quantity_unit_collision_maps_once_and_survives_json(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader(is_test=True)
    uploader._test_qid_map = {"Q174789": "Q900", "Q900": "Q901"}
    uploader._test_pid_map = {pid_map_key("P2048", "quantity"): "P2048"}
    uploader._test_property_datatypes = {"P2048": "quantity"}
    uploader._test_entity_exists = {"Q900": True, "Q901": True}
    monkeypatch.setattr(uploader, "_ensure_test_maps_for_item", lambda _, **_kwargs: WikiTestAdaptStats())
    item = WikidataItem(statements=[WikidataStatement("P2048", "12", "quantity", unit="mm")])
    mapped, result = uploader._adapt_item_for_test_wiki(item)
    assert not result.skipped
    assert mapped.statements[0].unit == "http://test.wikidata.org/entity/Q900"
    restored = WikidataStatement(**json.loads(json.dumps(asdict(mapped.statements[0]))))
    assert uploader._build_claim(restored).get_json()["mainsnak"]["datavalue"]["value"]["unit"] == (
        "http://test.wikidata.org/entity/Q900"
    )
    with pytest.raises(ValueError, match="ambiguous wiki namespace"):
        uploader._quantity_unit_uri("Q900")
    with pytest.raises(ClaimBuildError, match="quantity unit"):
        uploader._quantity_unit_uri("unknown-unit")


def test_time_reference_preserves_calendar_and_uncertainty() -> None:
    snak = {"property": "P813", "value": "+2020-01-02T00:00:00Z", "type": "time", "precision": 11,
            "calendarmodel": "http://www.wikidata.org/entity/Q1985786", "before": 2, "after": 3, "timezone": 60}
    claim = _uploader()._build_claim(_statement("P571", "x", "string", [[snak]]))
    value = claim.get_json()["references"][0]["snaks"]["P813"][0]["datavalue"]["value"]
    assert (value["calendarmodel"], value["before"], value["after"], value["timezone"]) == (
        snak["calendarmodel"], 2, 3, 60,
    )
    with pytest.raises(ClaimBuildError):
        _uploader(is_test=True)._build_claim(_statement("P571", "x", "string", [[snak]]))


@pytest.mark.parametrize("metadata", [{"before": -1}, {"after": True}, {"timezone": "0"},
                                      {"calendarmodel": "Q1985786"}, {"precision": 0}])
def test_time_reference_rejects_unrepresentable_metadata(metadata: dict[str, object]) -> None:
    snak = {"property": "P813", "value": "+2020-01-02T00:00:00Z", "type": "time", **metadata}
    with pytest.raises(ClaimBuildError):
        _uploader()._build_claim(_statement("P571", "x", "string", [[snak]]))


def _remote_entity(claims: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    return {
        "id": "Q123", "type": "item", "lastrevid": 1,
        "labels": {}, "descriptions": {}, "aliases": {}, "claims": claims, "sitelinks": {},
    }


def _commit(remote: dict[str, object], result: object, *, fail: bool = False) -> None:
    if fail:
        raise RuntimeError("write rejected")
    remote["claims"] = {
        "P1574": [claim.get_json() for claim in result.claims.get("P1574") or [] if not claim.removed],
    }


def _p1574_values(entity: object) -> list[str]:
    return [claim.mainsnak.datavalue["value"]["id"] for claim in entity.claims.get("P1574") or [] if not claim.removed]


def test_deferred_p1574_keeps_remote_claim_until_pass2_write(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader()
    uploader._is_our_item_cache["Q123"] = True
    old_claim = uploader._build_claim(_statement("P1574", "Q1", "item", []))
    old_json = old_claim.get_json()
    old_json["id"] = "Q123$00000000-0000-0000-0000-000000000001"
    remote = _remote_entity({"P1574": [old_json]})
    monkeypatch.setattr(uploader, "_init_wbi", lambda: SimpleNamespace(
        item=SimpleNamespace(get=lambda *args, **kwargs: WikibaseIntegrator().item.new().from_json(
            copy.deepcopy(remote),
        )),
    ))

    pass1, _, _ = uploader._build_wbi_item(
        WikidataItem(existing_qid="Q123", statements=[WikidataStatement("P31", "Q5", "item")]),
        read_only=True,
    )
    assert _p1574_values(pass1) == ["Q1"]
    _commit(remote, pass1)
    assert [c["mainsnak"]["datavalue"]["value"]["id"] for c in remote["claims"]["P1574"]] == ["Q1"]

    deferred = WikidataItem(existing_qid="Q123", statements=[WikidataStatement("P1574", "Q2", "item")])
    pass2_fail, _, _ = uploader._build_wbi_item(deferred, read_only=True)
    assert _p1574_values(pass2_fail) == ["Q2"]
    with pytest.raises(RuntimeError):
        _commit(remote, pass2_fail, fail=True)
    assert [c["mainsnak"]["datavalue"]["value"]["id"] for c in remote["claims"]["P1574"]] == ["Q1"]

    pass2_ok, _, _ = uploader._build_wbi_item(deferred, read_only=True)
    _commit(remote, pass2_ok)
    assert [c["mainsnak"]["datavalue"]["value"]["id"] for c in remote["claims"]["P1574"]] == ["Q2"]
