"""Canonical author identity, deferred rank, and mutation safety."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import pytest

import converter.wikidata.item_builder  # noqa: F401  (import order)
from app.pipeline.hmo_canonical_wikidata import (
    CanonicalStudioContext,
    _sanitize_work_authors_against_context,
)
from app.pipeline.wikidata_local_refs import resolve_local_references
from app.pipeline.wikidata_upload_job import _deferred_author_fallback
from converter.wikidata.item_models import WikidataItem, WikidataStatement


@pytest.fixture(autouse=True)
def _stub_studio_source_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    async def get_cache_row(*args, **kwargs):
        return SimpleNamespace(
            input_fingerprint="a" * 64,
            summary={"overrides_applied": True, "source_items_sha256": "b" * 64},
        )

    async def cache_is_current(*args, **kwargs):
        return True

    monkeypatch.setattr("app.routers.wikidata_studio._get_studio_cache_row", get_cache_row)
    monkeypatch.setattr("app.routers.wikidata_studio._studio_cache_is_current", cache_is_current)


def _context() -> CanonicalStudioContext:
    return CanonicalStudioContext(marc_by_cn={"990001": {
        "title": "Example work",
        "authors": [{"name": "Alice", "role": "author"}],
        "marc_authority_matches": [{
            "name": "Alice", "role": "author", "approved": True,
            "entity_kind": "person", "wikidata_qid": "Q42",
        }],
    }})


def _work_author() -> WikidataStatement:
    source = [{"property": "P854", "value": "https://example.org/source", "type": "url"}]
    return WikidataStatement(
        "P50", "__LOCAL:person:1", "item", rank="preferred",
        qualifiers=[{"property": "P958", "value": "1r", "type": "string"}],
        references=source, reference_blocks=[source],
    )


def _work(claim: WikidataStatement) -> WikidataItem:
    return WikidataItem(
        local_id="work:1", entity_type="work", labels={"en": "Example work"},
        records=["990001"], statements=[claim],
    )


def test_context_cleanup_keeps_local_author_with_matching_public_qid_and_evidence() -> None:
    claim = _work_author()
    snapshot = deepcopy(claim)
    work = _work(claim)
    person = WikidataItem(local_id="person:1", entity_type="person", existing_qid="Q42")

    assert _sanitize_work_authors_against_context([work, person], _context()) == 0
    assert work.statements == [snapshot]


@pytest.mark.parametrize("context", [None, CanonicalStudioContext(marc_by_cn={})])
def test_local_author_without_record_context_blocks_without_mutation(context) -> None:
    work = _work(_work_author())
    person = WikidataItem(local_id="person:1", entity_type="person", existing_qid="Q42")
    original = deepcopy(work)
    with pytest.raises(ValueError, match="AUTHOR.*(identity|evidence)"):
        _sanitize_work_authors_against_context([work, person], context)
    assert work == original


@pytest.mark.parametrize("location", ["person", "record"])
@pytest.mark.parametrize("uri", [
    "https://test.wikidata.org/entity/Q42", "https://other.example/entity/Q42",
])
def test_local_author_does_not_confuse_foreign_qids_with_wikidata(
    location: str, uri: str,
) -> None:
    work = _work(_work_author())
    person = WikidataItem(local_id="person:1", entity_type="person", existing_qid="Q42")
    context = _context()
    if location == "person":
        person.existing_qid = uri
    else:
        context.marc_by_cn["990001"]["marc_authority_matches"][0]["wikidata_qid"] = uri
        source = [{"property": "P3959", "value": "990001", "type": "external-id"}]
        work.statements[0].references = deepcopy(source)
        work.statements[0].reference_blocks = [deepcopy(source)]
        person.labels = {"en": "Alice"}
        person.records = ["990001"]
    original = deepcopy(work)
    with pytest.raises(ValueError, match="AUTHOR.*(identity|evidence)"):
        _sanitize_work_authors_against_context([work, person], context)
    assert work == original


def test_local_author_accepts_explicit_public_wikidata_authority_uri() -> None:
    work = _work(_work_author())
    person = WikidataItem(local_id="person:1", entity_type="person", existing_qid="Q42")
    context = _context()
    context.marc_by_cn["990001"]["marc_authority_matches"][0]["wikidata_qid"] = (
        "https://www.wikidata.org/entity/Q42"
    )
    original = deepcopy(work)
    assert _sanitize_work_authors_against_context([work, person], context) == 0
    assert work == original


@pytest.mark.parametrize("location", ["person", "record"])
@pytest.mark.parametrize("kind", ["local", "mapped-test"])
def test_test_wiki_qid_markers_do_not_prove_public_author_identity(
    location: str, kind: str,
) -> None:
    from converter.wikidata.test_wiki_compat import LocalTargetQid, MappedTestQid

    source = [{"property": "P3959", "value": "990001", "type": "external-id"}]
    claim = WikidataStatement("P50", "__LOCAL:person:1", "item", references=source,
                              reference_blocks=[source])
    work = _work(claim)
    person = WikidataItem(local_id="person:1", entity_type="person", existing_qid="Q42",
                          labels={"en": "Alice"}, records=["990001"],
                          statements=[WikidataStatement("P214", "123", "external-id")])
    context = _context()
    marker = LocalTargetQid("Q42", "person:1") if kind == "local" else MappedTestQid("Q42")
    if location == "person":
        person.existing_qid = marker
    else:
        context.marc_by_cn["990001"]["marc_authority_matches"][0]["wikidata_qid"] = marker
    with pytest.raises(ValueError, match="AUTHOR.*(identity|evidence)"):
        _sanitize_work_authors_against_context([work, person], context)
    assert work.statements == [claim]


def test_canonical_build_keeps_verified_local_author_and_its_metadata() -> None:
    from app.pipeline.hmo_canonical_wikidata import build_canonical_studio_result

    claim = _work_author()
    work = _work(claim)
    person = WikidataItem(
        local_id="person:1", entity_type="person", existing_qid="Q42",
        labels={"en": "Alice"}, statements=[WikidataStatement("P31", "Q5", "item")],
    )
    result = build_canonical_studio_result(
        [], context=_context(), reconcile=False,
        legacy_native_items=[work, person], return_native=True,
    )
    restored = next(item for item in result["native_items"] if item.local_id == "work:1")
    assert restored.statements == [claim]
    assert work.statements == [claim]


@pytest.mark.parametrize("metadata", [
    {"qualifiers": [{"property": "P958", "value": "1r"}]},
    {"references": [{"property": "P854", "value": "https://example.org"}]},
    {"reference_blocks": [[{"property": "P854", "value": "https://example.org"}]]},
    {"rank": "preferred"},
])
def test_hmo_author_mapping_blocks_unmapped_metadata(metadata) -> None:
    from app.pipeline.hmo_canonical import normalize_live_entity
    from app.pipeline.hmo_canonical_wikidata import native_wikidata_claims

    entity = normalize_live_entity({
        "local_id": "work:1", "source_uri": "https://example.org/work:1",
        "wikibase_id": "Q1", "entity_type": "F1_Work", "labels": {"en": "Example work"},
        "claims": [{"wikidata_property": "P50", "value_type": "item", "value": "Q42", **metadata}],
    })
    with pytest.raises(ValueError, match="AUTHOR.*metadata"):
        native_wikidata_claims(entity)


def test_hmo_unmapped_local_author_with_metadata_cannot_disappear() -> None:
    from app.pipeline.hmo_canonical import normalize_live_entity
    from app.pipeline.hmo_canonical_wikidata import native_wikidata_claims

    entity = normalize_live_entity({
        "local_id": "work:1", "source_uri": "https://example.org/work:1",
        "wikibase_id": "Q1", "entity_type": "F1_Work", "labels": {"en": "Example work"},
        "claims": [{"wikidata_property": "P50", "value_type": "item",
                    "value": "__LOCAL:person:1",
                    "qualifiers": [{"property": "P958", "value": "1r"}]}],
    })
    with pytest.raises(ValueError, match="AUTHOR.*metadata"):
        native_wikidata_claims(entity)


def test_hmo_unmapped_rollup_author_with_metadata_cannot_disappear(monkeypatch) -> None:
    from app.pipeline import hmo_canonical_wikidata as canonical
    from app.pipeline.hmo_canonical import normalize_live_entity

    work = normalize_live_entity({
        "local_id": "work:1", "source_uri": "https://example.org/work:1",
        "wikibase_id": "Q1", "entity_type": "F1_Work", "labels": {"en": "Example work"},
    })
    source = normalize_live_entity({
        "local_id": "creation:1", "source_uri": "https://example.org/creation:1",
        "wikibase_id": "Q2", "entity_type": "F27_Work_Creation",
        "claims": [{"wikidata_property": "P50", "value_type": "item",
                    "value": "__LOCAL:person:1",
                    "qualifiers": [{"property": "P958", "value": "1r"}]}],
    })
    monkeypatch.setattr(canonical, "_allowed_rollup_pids", lambda _source: frozenset({"P50"}))
    with pytest.raises(ValueError, match="AUTHOR.*metadata"):
        canonical.native_wikidata_claims(work, rollup_sources=[source])


def test_context_cleanup_keeps_verified_record_scoped_local_author_without_qid() -> None:
    source = [{"property": "P3959", "value": "990001", "type": "external-id"}]
    claim = WikidataStatement(
        "P50", "__LOCAL:person:1", "item", references=source, reference_blocks=[source],
        qualifiers=[{"property": "P1932", "value": "Alice", "type": "string"}],
    )
    person = WikidataItem(
        local_id="person:1", entity_type="person", labels={"en": "Alice"}, records=["990001"],
        statements=[WikidataStatement("P214", "123", "external-id")],
    )
    work = _work(claim)
    context = _context()
    context.marc_by_cn["990001"]["marc_authority_matches"][0].pop("wikidata_qid")
    snapshot = deepcopy(claim)

    assert _sanitize_work_authors_against_context([work, person], context) == 0
    assert work.statements == [snapshot]


def test_unverified_local_author_name_match_without_source_blocks() -> None:
    claim = WikidataStatement("P50", "__LOCAL:person:1", "item")
    person = WikidataItem(
        local_id="person:1", entity_type="person", labels={"en": "Alice"}, records=["990001"],
        statements=[WikidataStatement("P214", "123", "external-id")],
    )
    work = _work(claim)
    context = _context()
    context.marc_by_cn["990001"]["marc_authority_matches"][0].pop("wikidata_qid")
    with pytest.raises(ValueError, match="AUTHOR.*(identity|evidence)"):
        _sanitize_work_authors_against_context([work, person], context)
    assert work.statements == [claim]


def test_local_author_does_not_reuse_qid_for_another_record() -> None:
    source = [{"property": "P3959", "value": "990002", "type": "external-id"}]
    claim = WikidataStatement("P50", "__LOCAL:person:1", "item", references=source,
                              reference_blocks=[source])
    work = _work(claim)
    work.records.append("990002")
    person = WikidataItem(local_id="person:1", entity_type="person", existing_qid="Q42")
    context = _context()
    context.marc_by_cn["990002"] = {
        "title": "Example work", "authors": [{"name": "Alice", "role": "author"}],
        "marc_authority_matches": [{"name": "Alice", "role": "author", "approved": True,
                                     "entity_kind": "person", "wikidata_qid": "Q43"}],
    }
    with pytest.raises(ValueError, match="AUTHOR.*(identity|evidence)"):
        _sanitize_work_authors_against_context([work, person], context)
    assert work.statements == [claim]


@pytest.mark.parametrize("local", [True, False])
def test_qid_match_does_not_ignore_a_different_source_record(local: bool) -> None:
    blocks = [
        [{"property": "P3959", "value": "990002", "type": "external-id"}],
        [{"property": "P854", "value": "https://example.org/source", "type": "url"}],
    ]
    claim = WikidataStatement(
        "P50", "__LOCAL:person:1" if local else "Q42", "item",
        references=[snak for block in blocks for snak in block], reference_blocks=blocks,
    )
    work = _work(claim)
    work.records.append("990002")
    person = WikidataItem(local_id="person:1", entity_type="person", existing_qid="Q42")
    context = _context()
    context.marc_by_cn["990002"] = {"title": "Unrelated work"}
    with pytest.raises(ValueError, match="AUTHOR.*(identity|evidence)"):
        _sanitize_work_authors_against_context([work, person], context)
    assert work.statements == [claim]


def test_author_name_does_not_keep_an_unrelated_record_source() -> None:
    source = [{"property": "P3959", "value": "990002", "type": "external-id"}]
    claim = WikidataStatement("P2093", "Alice", "string", references=source,
                              reference_blocks=[source])
    work = _work(claim)
    work.records.append("990002")
    context = _context()
    context.marc_by_cn["990002"] = {"title": "Unrelated work"}
    with pytest.raises(ValueError, match="AUTHOR.*(identity|evidence)"):
        _sanitize_work_authors_against_context([work], context)
    assert work.statements == [claim]


def test_local_author_name_match_checks_other_person_aliases() -> None:
    source = [{"property": "P3959", "value": "990001", "type": "external-id"}]
    claim = WikidataStatement("P50", "__LOCAL:person:1", "item", references=source,
                              reference_blocks=[source])
    first = WikidataItem(
        local_id="person:1", entity_type="person", labels={"en": "Alice"}, records=["990001"],
        statements=[WikidataStatement("P214", "123", "external-id")],
    )
    second = WikidataItem(
        local_id="person:2", entity_type="person", labels={"en": "Other"},
        aliases={"en": ["Alice"]}, records=["990001"],
        statements=[WikidataStatement("P214", "456", "external-id")],
    )
    work = _work(claim)
    context = _context()
    context.marc_by_cn["990001"]["marc_authority_matches"][0].pop("wikidata_qid")
    with pytest.raises(ValueError, match="AUTHOR.*(identity|evidence)"):
        _sanitize_work_authors_against_context([work, first, second], context)
    assert work.statements == [claim]


@pytest.mark.parametrize(
    "case", ["missing", "wrong-type", "different-qid", "duplicate", "untrusted", "name-conflict"],
)
def test_context_cleanup_blocks_unverified_local_author_without_losing_evidence(case: str) -> None:
    work = _work(_work_author())
    original = deepcopy(work)
    person = WikidataItem(local_id="person:1", entity_type="person", existing_qid="Q42")
    if case == "wrong-type":
        person.entity_type = "work"
    elif case == "different-qid":
        person.existing_qid = "Q43"
    elif case == "untrusted":
        person.heading_mismatch = {"reason": "conflict"}
    elif case == "name-conflict":
        person.labels = {"en": "Bob"}
    items = [work] if case == "missing" else [work, person]
    if case == "duplicate":
        items.append(deepcopy(person))

    with pytest.raises(ValueError, match="AUTHOR.*(identity|evidence)"):
        _sanitize_work_authors_against_context(items, _context())
    assert work == original


def test_context_cleanup_blocks_unmatched_sourced_qid_instead_of_replacing_it() -> None:
    claim = _work_author()
    claim.value = "Q43"
    work = _work(claim)
    original = deepcopy(work)

    with pytest.raises(ValueError, match="AUTHOR.*(identity|evidence)"):
        _sanitize_work_authors_against_context([work], _context())
    assert work == original


def test_context_cleanup_rejects_inconsistent_author_reference_views() -> None:
    claim = _work_author()
    claim.reference_blocks[0][0]["value"] = "https://example.org/changed"
    work = _work(claim)
    original = deepcopy(work)
    person = WikidataItem(local_id="person:1", entity_type="person", existing_qid="Q42")
    with pytest.raises(ValueError, match="references and reference_blocks disagree"):
        _sanitize_work_authors_against_context([work, person], _context())
    assert work == original


def test_rejected_qid_of_another_author_does_not_authorize_removal() -> None:
    source = [{"property": "P3959", "value": "990001", "type": "external-id"}]
    wrong = WikidataStatement("P50", "Q43", "item", references=source, reference_blocks=[source])
    named = WikidataStatement("P2093", "Alice", "string", references=source,
                              reference_blocks=[source])
    work = _work(wrong)
    work.statements.append(named)
    context = _context()
    context.marc_by_cn["990001"]["marc_authority_matches"].append({
        "name": "Bob", "role": "author", "wikidata_qid": "Q43", "guard_flags": ["date_conflict"],
    })
    with pytest.raises(ValueError, match="AUTHOR.*(identity|evidence)"):
        _sanitize_work_authors_against_context([work], context)
    assert work.statements == [wrong, named]


@pytest.mark.parametrize("difference", ["qualifier", "rank"])
def test_rejected_qid_source_is_not_moved_to_an_incompatible_name(difference: str) -> None:
    source = [{"property": "P3959", "value": "990001", "type": "external-id"}]
    wrong = WikidataStatement("P50", "Q42", "item", references=source, reference_blocks=[source])
    named = WikidataStatement("P2093", "Alice", "string", references=source,
                              reference_blocks=[source])
    if difference == "qualifier":
        wrong.qualifiers = [{"property": "P958", "value": "1r", "type": "string"}]
    else:
        wrong.rank = "preferred"
    work = _work(wrong)
    work.statements.append(named)
    context = _context()
    context.marc_by_cn["990001"]["marc_authority_matches"][0]["guard_flags"] = ["date_conflict"]
    snapshot = deepcopy(work)
    with pytest.raises(ValueError, match="AUTHOR.*(identity|evidence)"):
        _sanitize_work_authors_against_context([work], context)
    assert work == snapshot


def test_context_cleanup_is_atomic_when_a_later_author_fails() -> None:
    first = _work(WikidataStatement("P2093", "Old name", "string"))
    second = _work(_work_author())
    second.local_id = "work:2"
    first_before, second_before = deepcopy(first), deepcopy(second)
    person = WikidataItem(local_id="person:1", entity_type="person", existing_qid="Q43")

    with pytest.raises(ValueError, match="AUTHOR.*(identity|evidence)"):
        _sanitize_work_authors_against_context([first, second, person], _context())
    assert first == first_before
    assert second == second_before


def test_initial_author_cleanup_does_not_mutate_before_rejecting_evidence() -> None:
    from app.pipeline.hmo_canonical import normalize_live_entity
    from app.pipeline.hmo_canonical_wikidata import _append_context_work_author

    entity = normalize_live_entity({
        "local_id": "work:1", "source_uri": "https://example.org/work:1",
        "wikibase_id": "Q1", "entity_type": "F1_Work", "labels": {"en": "Example work"},
        "control_numbers": ["990001"],
    })
    first = WikidataStatement("P2093", " Alice ", "string")
    source = [{"property": "P854", "value": "https://example.org/source", "type": "url"}]
    invalid = WikidataStatement("P50", "Q43", "item", references=source, reference_blocks=[source])
    claims = [first, invalid]
    snapshot = deepcopy(claims)
    with pytest.raises(ValueError, match="AUTHOR.*(identity|evidence)"):
        _append_context_work_author(entity, _context(), claims)
    assert claims == snapshot


def test_failed_canonical_build_does_not_change_input_items() -> None:
    from app.pipeline.hmo_canonical_wikidata import build_canonical_studio_result

    first = _work(WikidataStatement("P2093", "Old name", "string"))
    second = _work(_work_author())
    second.local_id = "work:2"
    person = WikidataItem(local_id="person:1", entity_type="person", existing_qid="Q43")
    inputs = [first, second, person]
    snapshot = deepcopy(inputs)
    with pytest.raises(ValueError, match="AUTHOR.*(identity|evidence)"):
        build_canonical_studio_result([], context=_context(), reconcile=False,
                                      legacy_native_items=inputs)
    assert inputs == snapshot


@pytest.mark.parametrize("rank", ["preferred", "deprecated", "", "unknown"])
def test_deferred_author_fallback_rejects_non_normal_rank(rank: str) -> None:
    claim = WikidataStatement("P50", "__LOCAL:person:1", "item", rank=rank)
    original = deepcopy(claim)
    assert _deferred_author_fallback(claim, claim, {"person:1": "Alice"}) is None
    assert claim == original


def test_deferred_author_fallback_rejects_rewritten_metadata() -> None:
    original = WikidataStatement("P50", "__LOCAL:person:1", "item")
    rewritten = WikidataStatement(
        "P50", "__LOCAL:person:1", "item",
        qualifiers=[{"property": "P958", "value": "1r", "type": "string"}],
    )
    assert _deferred_author_fallback(original, rewritten, {"person:1": "Alice"}) is None


def test_deferred_author_fallback_rejects_non_normal_rewritten_rank() -> None:
    original = WikidataStatement("P50", "__LOCAL:person:1", "item")
    rewritten = WikidataStatement("P50", "__LOCAL:person:1", "item", rank="preferred")
    assert _deferred_author_fallback(original, rewritten, {"person:1": "Alice"}) is None


@pytest.mark.parametrize("kind", ["string", "time", "quantity"])
def test_deferred_author_fallback_rejects_non_item_values(kind: str) -> None:
    claim = WikidataStatement("P50", "__LOCAL:person:1", kind)
    assert _deferred_author_fallback(claim, claim, {"person:1": "Alice"}) is None


@pytest.mark.parametrize("target", ["", "person: 1"])
def test_deferred_author_fallback_rejects_invalid_local_ids(target: str) -> None:
    claim = WikidataStatement("P50", f"__LOCAL:{target}", "item")
    assert _deferred_author_fallback(claim, claim, {target: "Alice"}) is None


def test_deferred_author_fallback_keeps_normal_unsourced_case() -> None:
    claim = WikidataStatement("P50", "__LOCAL:person:1", "item")
    fallback = _deferred_author_fallback(claim, claim, {"person:1": "Alice"})
    assert fallback is not None
    assert (fallback.property_id, fallback.value, fallback.rank) == ("P2093", "Alice", "normal")


@pytest.mark.asyncio
@pytest.mark.parametrize("rank", ["preferred", "deprecated"])
async def test_deferred_job_reports_non_normal_author_unresolved(
    rank: str, sample_run, db_session, monkeypatch,
) -> None:
    from app.models.run_job import RunJob
    from app.pipeline import wikidata_upload as upload
    from app.pipeline.wikidata_upload_job import run_wikidata_upload_job

    claim = WikidataStatement("P50", "__LOCAL:author", "item", rank=rank)
    work = WikidataItem(local_id="source", entity_type="work", labels={"en": "Work"},
                        statements=[claim])
    person = WikidataItem(local_id="author", entity_type="person", labels={"en": "Alice"})
    first = upload.UploadOutcome("source", "Work", "work", "Q123", "updated", "ok", [],
                                 ownership="own")
    skipped = upload.UploadOutcome("author", "Alice", "person", None, "skipped", "ok", [])
    job = RunJob(
        project_id=sample_run["project_id"], run_id=sample_run["run_id"],
        kind="wikidata_upload", status="running", progress={}, created_by=sample_run["user_id"],
        params={"upload_target": "dry_run", "_wikidata_token": "fake", "_step1_state": {
            "outcomes": [dict(first.__dict__), dict(skipped.__dict__)],
            "created_qids": {"source": "Q123"}, "deferred": {"source": [asdict(claim)]},
        }},
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    async def build(*_args, **_kwargs):
        return [work, person]

    async def no_write(*_args, **_kwargs):
        raise AssertionError("The unresolved author must not be written")

    monkeypatch.setattr("app.routers.wikidata_studio._build_native_items", build)
    monkeypatch.setattr(upload, "_make_reconciler", lambda: SimpleNamespace(_cache={}))
    monkeypatch.setattr(upload, "upload_items", no_write)
    monkeypatch.setattr("app.pipeline.wikidata_existence.prefetch_entities_for_upload",
                        lambda *_args, **_kwargs: ({}, {}))
    monkeypatch.setattr(upload, "build_upload_corpus_quality_preflight", lambda items:
                        upload.UploadCorpusQualityPreflight(decisions={
                            item.local_id: upload.UploadQualityDecision(True) for item in items
                        }))

    await run_wikidata_upload_job(job.id)
    await db_session.refresh(job)
    assert job.status == "succeeded"
    assert job.result["link_outcomes"][0]["status"] == "unresolved"
    assert job.result["link_outcomes"][0]["property_id"] == "P50"
    assert (claim.property_id, claim.value, claim.rank) == ("P50", "__LOCAL:author", rank)


def test_local_reference_resolution_is_atomic_when_progress_validation_fails() -> None:
    first = WikidataItem(
        local_id="ms:1", entity_type="manuscript",
        statements=[WikidataStatement("P1574", "__LOCAL:work:missing", "item")],
    )
    others = [WikidataItem(local_id=f"ms:{i}", entity_type="manuscript") for i in range(2, 27)]
    items = [first, *others]
    original = deepcopy(items)

    def stop(_done: int, _total: int) -> None:
        raise ValueError("progress validation failed")

    with pytest.raises(ValueError, match="progress validation failed"):
        resolve_local_references(items, on_progress=stop)
    assert items == original
