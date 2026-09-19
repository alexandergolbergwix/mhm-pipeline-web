"""Tests for the rule-based verification engine, persistence, and job glue."""

from __future__ import annotations

import pytest

from app.pipeline.rule_verify.base import (
    RULE_STATES,
    RuleResult,
    fail,
    not_relevant,
    worst_state,
)
from app.pipeline.rule_verify.engine import RuleEngine
from app.pipeline.rule_verify.persist import build_rule_verdict, summary_from_results
from app.pipeline.rule_verify.rules.hmo import build_hmo_rules, in_run_dup_index
from app.pipeline.rule_verify.scope import build_context


def _item(**overrides):  # type: ignore[no-untyped-def]
    base = {
        "local_id": "QDraft_MS1",
        "labels": {"en": "Codex A", "he": "כתב יד א"},
        "descriptions": {"en": "A Hebrew manuscript"},
        "class_qid": "Q1",
        "entity_type": "Manuscript",
        "source_uri": "urn:hmo:ms1",
        "claims": [
            {"property_id": "P10", "datatype": "wikibase-item", "value": "Q2"},
            {"property_id": "P11", "datatype": "external-id", "value": "clean-id"},
        ],
        "shacl_issues": [],
        "control_numbers": ["CN1"],
        "skipped_statements": [],
    }
    base.update(overrides)
    return base


def _engine(items, *, api=False, fetcher=None):  # type: ignore[no-untyped-def]
    ctx = build_context(run_id="r1", items=items, api_enabled=api, fetcher=fetcher)
    ctx.marc_index = {"CN1": {"title": "Codex A"}}
    return RuleEngine(build_hmo_rules(), ctx)


def test_catalog_has_stable_shape() -> None:
    rules = build_hmo_rules()
    ids = [r.id for r in rules]
    assert len(ids) == len(set(ids))
    assert "hmo.shacl.blocking" in ids
    assert "hmo.duplicate.in_run" in ids
    assert any(r.uses_api for r in rules)
    assert len(rules) >= 25


def test_engine_reports_fail_pass_and_relevance() -> None:
    items = [
        _item(),
        _item(local_id="QDraft_MS2", claims=[], skipped_statements=["unresolved X"]),
    ]
    results = _engine(items).run_scope(items)
    by_item = {lid: {r.rule_id: r for r in rows} for lid, rows in results.items()}

    assert by_item["QDraft_MS1"]["hmo.marc.label_grounded"].state == "pass"
    assert by_item["QDraft_MS1"]["hmo.source_uri.present"].state == "pass"

    # Bad datatype value: external-id with a space.
    items[0]["claims"][1]["value"] = "has space"
    results = _engine(items).run_scope(items)
    by_item = {lid: {r.rule_id: r for r in rows} for lid, rows in results.items()}
    dt = by_item["QDraft_MS1"]["hmo.claims.datatype"]
    assert dt.state == "fail"
    assert dt.field == "claims"

    # Structural entities may be claim-free.
    structural = [_item(entity_type="E52_Time-Span", claims=[])]
    results = _engine(structural).run_scope(structural)
    by_item = {lid: {r.rule_id: r for r in rows} for lid, rows in results.items()}
    assert by_item["QDraft_MS1"]["hmo.claims.present"].state == "not_relevant"


def test_within_run_duplicate_rule_uses_precomputed_index() -> None:
    items = [
        _item(local_id="A", labels={"en": "Same Title"}),
        _item(local_id="B", labels={"en": "same  title"}),
    ]
    ctx = build_context(run_id="r1", items=items, api_enabled=False)
    assert ctx.in_run_dup_index  # twins precomputed
    engine = RuleEngine(build_hmo_rules(), ctx)
    results = engine.run_scope(items)
    for rows in results.values():
        dup = next(r for r in rows if r.rule_id == "hmo.duplicate.in_run")
        assert dup.state == "fail"
        assert dup.evidence["duplicates"][0]["local_id"] in {"A", "B"}


def test_shacl_rules_split_blocking_from_warning() -> None:
    item = _item(shacl_issues=[
        {"severity": "Violation", "message": "v1"},
        {"severity": "Warning", "message": "w1"},
    ])
    results = _engine([item]).run_scope([item])
    row = {r.rule_id: r for r in results["QDraft_MS1"]}
    assert row["hmo.shacl.blocking"].state == "fail"
    assert row["hmo.shacl.warning"].state == "fail"


def test_export_quality_codes_surface_as_rules() -> None:
    generic = _item(descriptions={"en": "In the Hebrew Manuscripts Ontology (HMO)"})
    results = _engine([generic]).run_scope([generic])
    row = {r.rule_id: r for r in results["QDraft_MS1"]}
    assert row["hmo.quality.generic_hmo_description"].state == "fail"

    latin_he = _item(labels={"he": "Latin only title"}, descriptions={"en": "desc"})
    results = _engine([latin_he]).run_scope([latin_he])
    row = {r.rule_id: r for r in results["QDraft_MS1"]}
    assert row["hmo.quality.latin_label_in_he"].state == "fail"
    assert row["hmo.label.language"].state == "fail"


def test_api_rules_fail_closed_without_fetcher() -> None:
    item = _item(wikibase_id="Q9")
    results = _engine([item], api=False).run_scope([item], include_api=True)
    row = {r.rule_id: r for r in results["QDraft_MS1"]}
    assert row["hmo.live.alive"].state == "error"
    assert "did not execute" in row["hmo.live.alive"].message


def test_api_rules_run_with_fetcher_and_report_dead_qid() -> None:
    def fetcher(call, arg=None):  # type: ignore[no-untyped-def]
        if call == "confirm_qids_alive":
            return {"Q2": False}
        if call == "inlabel_search":
            return [{"qid": "Q777", "label": arg}]
        raise AssertionError(f"unexpected call {call!r}")

    item = _item()
    results = _engine([item], api=True, fetcher=fetcher).run_scope([item], include_api=True)
    row = {r.rule_id: r for r in results["QDraft_MS1"]}
    alive = row["hmo.wikidata.qids_alive"]
    assert alive.state == "fail"
    assert alive.evidence["dead"] == ["Q2"]
    cand = row["hmo.wikidata.label_candidates"]
    assert cand.state == "fail"
    assert cand.evidence["candidates"][0]["qid"] == "Q777"


def test_worst_state_ordering() -> None:
    assert worst_state(["pass", "fail"]) == "fail"
    assert worst_state(["not_relevant", "pass"]) == "pass"
    assert worst_state([]) == "not_relevant"
    assert worst_state(["error", "pass"]) == "error"


def test_summary_shape_and_rule_verdict_compaction() -> None:
    results = {
        "A": [
            fail("r1", "labels", "bad"),
            not_relevant("r2"),
            RuleResult("r3", "pass"),
        ],
    }
    summary = summary_from_results(results)
    assert summary["scope"] == 1
    assert summary["overall_counts"]["fail"] == 1
    assert summary["per_rule"]["r1"]["fail"] == 1

    verdict = build_rule_verdict(results["A"], job_id="j1", checked_at="now")
    assert verdict["schema"] == "rule_verdict_v1"
    assert verdict["overall"] == "fail"
    entry = next(r for r in verdict["results"] if r["rule_id"] == "r3")
    assert set(entry) == {"rule_id", "state"}
    failing = next(r for r in verdict["results"] if r["rule_id"] == "r1")
    assert failing["field"] == "labels" and failing["message"] == "bad"


def test_states_are_the_public_contract() -> None:
    assert set(RULE_STATES) == {"pass", "fail", "not_relevant", "error"}


def test_in_run_dup_index_ignores_singletons() -> None:
    items = [_item(local_id="A", labels={"en": "X"}), _item(local_id="B", labels={"en": "Y"})]
    assert in_run_dup_index(items) == {}


@pytest.mark.asyncio
async def test_persist_rule_verdicts_writes_override_rows(db_session, sample_run) -> None:
    from sqlalchemy import select

    from app.models.hmo_studio_item_override import HmoStudioItemOverride
    from app.pipeline.rule_verify.persist import persist_rule_verdicts

    run_id = sample_run["run_id"]
    results = {"QDraft_MS1": [fail("r1", "labels", "bad"), RuleResult("r2", "pass")]}
    written = await persist_rule_verdicts(db_session, run_id=run_id, results_by_local_id=results)
    assert written == 1
    row = (
        await db_session.execute(
            select(HmoStudioItemOverride).where(
                HmoStudioItemOverride.run_id == run_id,
                HmoStudioItemOverride.local_id == "QDraft_MS1",
            )
        )
    ).scalar_one()
    assert row.rule_verdict["overall"] == "fail"
    assert row.rule_verdict["job_id"] is None
    assert row.rule_verdict_at is not None


def test_merge_summaries_combines_shards() -> None:
    from app.pipeline.rule_verify_job import merge_summaries

    merged = merge_summaries([
        {"scope": 2, "overall_counts": {"fail": 1, "pass": 1, "not_relevant": 0, "error": 0},
         "per_rule": {"r1": {"fail": 1, "pass": 1, "not_relevant": 0, "error": 0}}},
        {"scope": 1, "overall_counts": {"fail": 0, "pass": 1, "not_relevant": 0, "error": 0},
         "per_rule": {"r1": {"fail": 0, "pass": 1, "not_relevant": 0, "error": 0}}},
    ])
    assert merged["scope"] == 3
    assert merged["overall_counts"]["pass"] == 2
    assert merged["per_rule"]["r1"]["fail"] == 1
    assert merged["per_rule"]["r1"]["pass"] == 2


@pytest.mark.asyncio
async def test_rule_verify_endpoints(sample_run, db_session) -> None:
    """Catalog + results endpoints answer; settings round-trip per user."""
    run_id = sample_run["run_id"]
    client = sample_run["client"]

    catalog = await client.get(f"/api/runs/{run_id}/hmo-studio/items/rule-verify/catalog")
    assert catalog.status_code == 200
    ids = [row["id"] for row in catalog.json()]
    assert "hmo.shacl.blocking" in ids

    # No item build yet → 409 like every other Studio surface.
    results = await client.get(f"/api/runs/{run_id}/hmo-studio/items/rule-verify/results")
    assert results.status_code == 409

    settings_get = await client.get("/api/me/rule-verify-settings")
    assert settings_get.status_code == 200
    assert settings_get.json()["blocked_rules"] == {}

    settings_put = await client.put(
        "/api/me/rule-verify-settings",
        json={"blocked_rules": {"hmo.shacl.blocking": True}},
    )
    assert settings_put.status_code == 200
    assert settings_put.json()["blocked_rules"] == {"hmo.shacl.blocking": True}
