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
        _item(local_id="A", labels={"en": "Same Title"},
              source_uri="https://w3id.org/mhm/ontology#ExprA_in_CN1"),
        _item(local_id="B", labels={"en": "same  title"},
              source_uri="https://w3id.org/mhm/ontology#ExprB_in_CN1"),
    ]
    ctx = build_context(run_id="r1", items=items, api_enabled=False)
    assert ctx.in_run_dup_index  # twins precomputed
    engine = RuleEngine(build_hmo_rules(), ctx)
    results = engine.run_scope(items)
    for rows in results.values():
        dup = next(r for r in rows if r.rule_id == "hmo.duplicate.in_run")
        assert dup.state == "fail"
        assert dup.evidence["duplicates"][0]["local_id"] in {"A", "B"}


def test_within_run_duplicate_skips_corpus_shared_nodes() -> None:
    """Corpus-shared nodes (no CN in their source URI) have no record
    identity: same-title/different-author works must not read as twins."""
    shared_cns = ["990000403370205171", "990000403660205171"]
    items = [
        _item(local_id="W1", labels={"he": "שלחן ערוך (יורה דעה)"},
              source_uri="https://w3id.org/mhm/ontology#Work_X_by_37",
              control_numbers=shared_cns),
        _item(local_id="W2", labels={"he": "שלחן ערוך (יורה דעה)"},
              source_uri="https://w3id.org/mhm/ontology#Work_Y_by_38",
              control_numbers=shared_cns),
        # Keep the index non-empty so the per-entity gate (not the empty-index
        # early return) handles the corpus-shared node.
        _item(local_id="E1", labels={"en": "Shared Title"},
              source_uri="https://w3id.org/mhm/ontology#ExprE1_in_CN1"),
        _item(local_id="E2", labels={"en": "Shared Title"},
              source_uri="https://w3id.org/mhm/ontology#ExprE2_in_CN1"),
    ]
    ctx = build_context(run_id="r1", items=items, api_enabled=False)
    assert ctx.in_run_dup_index
    results = RuleEngine(build_hmo_rules(), ctx).run_scope(items)
    dup_w1 = next(r for r in results["W1"] if r.rule_id == "hmo.duplicate.in_run")
    assert dup_w1.state == "not_relevant"
    dup_e1 = next(r for r in results["E1"] if r.rule_id == "hmo.duplicate.in_run")
    assert dup_e1.state == "fail"


def test_within_run_duplicate_key_uses_own_record_cn() -> None:
    """Regression (run 3494ebf5 re-measure, 536 fails): shared hubs inherit
    the whole corpus's CN set, so ``control_numbers[0]`` is not a record
    identity — a 'תכלאל' expression per manuscript all carried the same
    first CN and read as one duplicate group. The key must use the item's
    own CN (the one present in its source URI)."""
    shared_cns = ["990000403370205171", "990000403660205171"]
    items = [
        _item(
            local_id="A",
            labels={"he": "תכלאל"},
            source_uri="https://w3id.org/mhm/ontology#Expression_in_990000403660205171",
            control_numbers=shared_cns,
        ),
        _item(
            local_id="B",
            labels={"he": "תכלאל"},
            source_uri="https://w3id.org/mhm/ontology#Expression_in_990000409260205171",
            control_numbers=shared_cns,
        ),
    ]
    ctx = build_context(run_id="r1", items=items, api_enabled=False)
    assert not ctx.in_run_dup_index  # distinct own records → no twins
    engine = RuleEngine(build_hmo_rules(), ctx)
    results = engine.run_scope(items)
    for rows in results.values():
        dup = next(r for r in rows if r.rule_id == "hmo.duplicate.in_run")
        assert dup.state == "pass"

    # Same record + same label is still a twin.
    twin_b = _item(
        local_id="B",
        labels={"he": "תכלאל"},
        source_uri="https://w3id.org/mhm/ontology#Expression2_in_990000403660205171",
        control_numbers=shared_cns,
    )
    items[1] = twin_b
    ctx = build_context(run_id="r1", items=items, api_enabled=False)
    assert ctx.in_run_dup_index
    results = RuleEngine(build_hmo_rules(), ctx).run_scope(items)
    dup = next(r for r in results["A"] if r.rule_id == "hmo.duplicate.in_run")
    assert dup.state == "fail"


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

    # Summary reads override rows only — no build required, all unchecked.
    results = await client.get(f"/api/runs/{run_id}/hmo-studio/items/rule-verify/results")
    assert results.status_code == 200
    body = results.json()
    assert set(body["overall_counts"]) >= {"pass", "fail", "not_relevant", "error", "unchecked"}
    # The paginated entity endpoint 400s for the unsupported unchecked state.
    entities = await client.get(
        f"/api/runs/{run_id}/hmo-studio/items/rule-verify/results/entities?state=unchecked"
    )
    assert entities.status_code == 400

    settings_get = await client.get("/api/me/rule-verify-settings")
    assert settings_get.status_code == 200
    assert settings_get.json()["blocked_rules"] == {}

    settings_put = await client.put(
        "/api/me/rule-verify-settings",
        json={"blocked_rules": {"hmo.shacl.blocking": True}},
    )
    assert settings_put.status_code == 200
    assert settings_put.json()["blocked_rules"] == {"hmo.shacl.blocking": True}


@pytest.mark.asyncio
async def test_rule_verify_single_entity_endpoint(sample_run, db_session) -> None:
    """The drawer endpoint returns one entity's non-pass results, passes counted."""
    from app.pipeline.rule_verify.persist import persist_rule_verdicts

    run_id = sample_run["run_id"]
    client = sample_run["client"]

    await persist_rule_verdicts(
        db_session,
        run_id=run_id,
        results_by_local_id={
            "QDraft_MS1": [fail("r1", "labels", "bad"), RuleResult("r2", "pass")],
        },
    )

    res = await client.get(
        f"/api/runs/{run_id}/hmo-studio/items/rule-verify/results/entities/QDraft_MS1"
    )
    assert res.status_code == 200
    body = res.json()
    assert body["local_id"] == "QDraft_MS1"
    assert body["overall"] == "fail"
    assert body["pass_count"] == 1
    assert [(r["rule_id"], r["state"]) for r in body["results"]] == [("r1", "fail")]
    assert body["results"][0]["field"] == "labels"
    assert body["results"][0]["message"] == "bad"

    missing = await client.get(
        f"/api/runs/{run_id}/hmo-studio/items/rule-verify/results/entities/QDraft_MISSING"
    )
    assert missing.status_code == 200
    assert missing.json()["overall"] == "unchecked"
    assert missing.json()["results"] == []


def test_verdict_rule_filter_binds_python_object() -> None:
    """The drill-down containment RHS must be a Python object, not a JSON string.

    Regression: the RHS was built with ``json.dumps(...)``, but asyncpg
    JSON-encodes JSONB bind values itself — the pre-dumped string
    double-encoded into a jsonb scalar, the filter matched nothing, and
    every drill-down showed "No entries match." while the summary
    (computed in Python) still counted the fails. The containment itself
    is Postgres-only, so this pins the bind value type instead.
    """
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.sql.elements import BindParameter

    from app.routers.hmo_studio_items import _verdict_filter_conditions

    conds = _verdict_filter_conditions(state="all", rules=["r1"], q="")
    stack, params = [conds[-1]], []
    while stack:
        node = stack.pop()
        if isinstance(node, BindParameter):
            params.append(node)
            continue
        stack.extend(node.get_children())
    assert params, "no bind parameter in the containment expression"
    # A pre-dumped JSON string would double-encode under asyncpg — the JSONB
    # bind must carry the Python object itself. (The subscript key param is
    # a plain str on purpose; string keys bind correctly.)
    jsonb_params = [p for p in params if isinstance(p.type, JSONB)]
    assert len(jsonb_params) == 1
    assert jsonb_params[0].value == [{"rule_id": "r1", "state": "fail"}]


def test_claim_datatype_accepts_wikibase_time_and_zero_quantity() -> None:
    """Regression (run 3494ebf5, 318 fails): the time shape applied its regex
    to ``str(dict)`` — every Wikibase ``time`` claim failed — and the quantity
    shape used ``amount or ""``, so the falsy ``0.0`` read as empty."""
    item = _item(claims=[
        {
            "property_id": "P38",
            "datatype": "time",
            "value": {"time": "+1572-00-00T00:00:00Z", "precision": 9},
        },
        {"property_id": "P232", "datatype": "quantity", "value": {"amount": 0.0}},
        {"property_id": "P265", "datatype": "quantity", "value": {"amount": 1.0}},
    ])
    results = _engine([item]).run_scope([item])
    row = {r.rule_id: r for r in results["QDraft_MS1"]}
    assert row["hmo.claims.datatype"].state == "pass"

    bad_time = _item(claims=[
        {"property_id": "P38", "datatype": "time", "value": {"time": "not-a-date", "precision": 9}},
    ])
    results = _engine([bad_time]).run_scope([bad_time])
    row = {r.rule_id: r for r in results["QDraft_MS1"]}
    assert row["hmo.claims.datatype"].state == "fail"


def test_production_fetcher_probe_passes_timeout() -> None:
    """Regression (run 3494ebf5, 2216 errors): the inlabel probe called
    ``_fetch_json(url)`` without the required ``timeout`` keyword, so every
    probe raised and the rule errored for the whole run."""
    import app.pipeline.wikidata_duplicate_probe as probe

    recorded: dict[str, object] = {}

    def fake_fetch_json(url: str, *, timeout: float) -> dict[str, object]:
        recorded["timeout"] = timeout
        return {"query": {"search": []}}

    original = (probe._fetch_json, probe._search_url)
    probe._fetch_json = fake_fetch_json  # type: ignore[assignment]
    probe._search_url = lambda *_a, **_k: "https://example.org/search"  # type: ignore[assignment]
    try:
        from app.pipeline.rule_verify.api_fetcher import production_fetcher

        production_fetcher()("inlabel_search", "Codex A")
    finally:
        probe._fetch_json, probe._search_url = original  # type: ignore[assignment]
    assert recorded["timeout"] == 30.0


def _engine_with_counters(items, counters: dict[str, int], *, api=True, fetcher=None):  # type: ignore[no-untyped-def]
    ctx = build_context(run_id="r1", items=items, api_enabled=api, fetcher=fetcher)
    ctx.marc_index = {"CN1": {"title": "Codex A"}}
    ctx.counters.update(counters)
    return RuleEngine(build_hmo_rules(), ctx)


def test_probe_budget_exhausted_is_not_relevant_not_error() -> None:
    """W-255 (run 3494ebf5, 17,450 errors): a protective budget cap is an
    operational guard, not a data defect — an unexecuted probe is
    ``not_relevant``, never ``error`` and never ``pass``."""

    def fetcher(call, arg=None):  # type: ignore[no-untyped-def]
        raise AssertionError(f"budget exhausted — fetcher must not run ({call!r})")

    items = [_item()]
    results = _engine_with_counters(
        items, {"wd_probe_budget": 0}, fetcher=fetcher,
    ).run_scope(items, include_api=True)
    row = {r.rule_id: r for r in results["QDraft_MS1"]}
    cand = row["hmo.wikidata.label_candidates"]
    assert cand.state == "not_relevant"
    assert "did not execute" in cand.message


def test_rate_limited_probe_is_not_relevant_not_error() -> None:
    """W-255: an HTTP 429 / network probe failure did not execute —
    fail-closed abstain (not_relevant), not an execution error."""

    def fetcher(call, arg=None):  # type: ignore[no-untyped-def]
        if call == "inlabel_search":
            raise RuntimeError("HTTP 429: too many requests")
        raise AssertionError(f"unexpected call {call!r}")

    items = [_item(claims=[])]
    results = _engine_with_counters(items, {}, fetcher=fetcher).run_scope(
        items, include_api=True,
    )
    row = {r.rule_id: r for r in results["QDraft_MS1"]}
    cand = row["hmo.wikidata.label_candidates"]
    assert cand.state == "not_relevant"
    assert "429" in cand.message


def test_qid_budget_exhausted_is_not_relevant_not_error() -> None:
    """W-255: the QID liveness budget cap abstains, not errors."""

    def fetcher(call, arg=None):  # type: ignore[no-untyped-def]
        raise AssertionError(f"budget exhausted — fetcher must not run ({call!r})")

    items = [_item()]
    results = _engine_with_counters(
        items, {"wd_qid_budget": 0}, fetcher=fetcher,
    ).run_scope(items, include_api=True)
    row = {r.rule_id: r for r in results["QDraft_MS1"]}
    alive = row["hmo.wikidata.qids_alive"]
    assert alive.state == "not_relevant"
    assert "did not execute" in alive.message


@pytest.mark.asyncio
async def test_load_rule_verify_scope_aborts_on_cancel(db_session) -> None:
    """Rule R28: the scope load is a long silent stretch — the cancel check
    must abort it before the first merge, not after the whole load."""
    import uuid

    from app.pipeline.run_job_service import JobCancelledError
    from app.pipeline.rule_verify.scope import load_rule_verify_scope

    async def cancelled() -> None:
        raise JobCancelledError("cancel requested")

    with pytest.raises(JobCancelledError):
        await load_rule_verify_scope(
            db_session, uuid.uuid4(), should_cancel=cancelled,
        )


# ── Rule W-256: CPU-only shards + single-container API pass ─────────────

def test_api_rule_ownership_is_stable() -> None:
    """The API pass owns exactly the ``uses_api`` rules — the merge strips
    and replaces entries for precisely this set."""
    api_ids = {r.id for r in build_hmo_rules() if r.uses_api}
    assert api_ids == {
        "hmo.live.alive",
        "hmo.live.label_drift",
        "hmo.wikidata.qids_alive",
        "hmo.wikidata.label_candidates",
    }


def test_api_only_scope_runs_only_api_rules() -> None:
    """The API-pass container runs only the API-backed rules over verdict
    rows a CPU pass already wrote — CPU rules must not re-run there."""

    def fetcher(call, arg=None):  # type: ignore[no-untyped-def]
        if call == "inlabel_search":
            return []
        if call == "confirm_qids_alive":
            return {"Q2": True}
        raise AssertionError(f"unexpected call {call!r}")

    items = [_item()]
    results = _engine(items, api=True, fetcher=fetcher).run_scope(
        items, api_only=True,
    )
    row = results["QDraft_MS1"]
    api_ids = {r.id for r in build_hmo_rules() if r.uses_api}
    assert {r.rule_id for r in row} == api_ids
    assert row[0].state in {"pass", "not_relevant"}


@pytest.mark.asyncio
async def test_shard_runs_cpu_rules_only(monkeypatch) -> None:
    """Rule W-256: a shard must never probe — every shard probing with its
    own fetcher split the budget four ways and stacked four request rates
    into Wikidata 429s (132 measured on run 45513a45)."""
    import uuid

    from app.pipeline import rule_verify_job as rvj

    seen: dict[str, object] = {}

    class _FakeSession:
        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

    async def _fake_scope(db, run_id, *, item_ids=None, **_kw):  # type: ignore[no-untyped-def]
        return [_item()]

    async def _fake_run_and_persist(**kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return {"scope": 1, "overall_counts": {}, "per_rule": {}}

    monkeypatch.setattr(rvj, "session_scope", lambda: _FakeSession())
    monkeypatch.setattr(rvj, "load_rule_verify_scope", _fake_scope)
    monkeypatch.setattr(rvj, "_run_and_persist", _fake_run_and_persist)

    summary = await rvj.run_rule_verify_shard("j1", uuid.uuid4(), ["QDraft_MS1"])
    assert seen["with_api"] is False
    assert summary["scope"] == 1


@pytest.mark.asyncio
async def test_api_pass_merges_and_reports_per_rule_only(monkeypatch) -> None:
    """The API pass: API rules only, per-chunk merge, per-rule-only summary
    (the shards already counted the entities — no second scope)."""
    import uuid

    from app.pipeline import rule_verify_job as rvj

    class _FakeSession:
        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

    async def _fake_scope(db, run_id, *, item_ids=None, **_kw):  # type: ignore[no-untyped-def]
        return [_item()]

    def _fake_fetcher() -> object:
        def fetch(call: object, arg: object = None) -> object:
            if call == "inlabel_search":
                return []
            if call == "confirm_qids_alive":
                return {"Q2": True}
            raise AssertionError(f"unexpected call {call!r}")

        return fetch

    merged_calls: list[dict[str, object]] = []

    async def _fake_merge(db, **kwargs):  # type: ignore[no-untyped-def]
        merged_calls.append(kwargs)
        return 1

    async def _fake_progress(job_id: object, progress: object) -> None:
        return None

    async def _fake_cancel(job_id: object) -> bool:
        return False

    monkeypatch.setattr(rvj, "session_scope", lambda: _FakeSession())
    monkeypatch.setattr(rvj, "load_rule_verify_scope", _fake_scope)
    monkeypatch.setattr(rvj, "merge_api_rule_verdicts", _fake_merge)
    monkeypatch.setattr(rvj, "update_job_progress", _fake_progress)
    monkeypatch.setattr(rvj, "is_cancel_requested", _fake_cancel)

    import app.pipeline.rule_verify.api_fetcher as api_fetcher

    monkeypatch.setattr(api_fetcher, "production_fetcher", _fake_fetcher)

    job_id = str(uuid.uuid4())
    summary = await rvj.run_rule_verify_api_pass(job_id, uuid.uuid4())
    assert summary["scope"] == 0
    assert summary["overall_counts"] == {}
    assert summary["probed"] == 1
    cand = summary["per_rule"]["hmo.wikidata.label_candidates"]
    assert cand["pass"] == 1
    assert merged_calls and merged_calls[0]["job_id"] == job_id


def test_api_pass_summary_merges_without_double_counting() -> None:
    """``merge_summaries`` must add the API pass's per-rule tallies but not
    a second copy of the scope or the per-entity overalls."""
    from app.pipeline.rule_verify.persist import per_rule_tally
    from app.pipeline.rule_verify_job import merge_summaries

    shard = {
        "scope": 2,
        "overall_counts": {"fail": 1, "pass": 1, "not_relevant": 0, "error": 0},
        "per_rule": {"cpu": {"fail": 1, "pass": 1, "not_relevant": 0, "error": 0}},
    }
    api = per_rule_tally({
        "A": [RuleResult("api1", "pass")],
        "B": [fail("api1", "labels", "x")],
    })
    merged = merge_summaries([shard, {"scope": 0, "overall_counts": {}, "per_rule": api}])
    assert merged["scope"] == 2
    assert merged["overall_counts"]["fail"] == 1
    assert merged["per_rule"]["api1"]["pass"] == 1
    assert merged["per_rule"]["api1"]["fail"] == 1


@pytest.mark.asyncio
async def test_merge_api_rule_verdicts_replaces_stale_api_entries(
    db_session, sample_run,
) -> None:
    """The merge strips stale API-rule entries (the pre-W-255 error wall),
    writes the fresh API answers, keeps CPU entries, and recomputes the
    rollups — a row must never mix a stale API answer with fresh CPU ones."""
    from sqlalchemy import select

    from app.models.hmo_studio_item_override import HmoStudioItemOverride
    from app.pipeline.rule_verify.persist import (
        merge_api_rule_verdicts,
        persist_rule_verdicts,
    )

    run_id = sample_run["run_id"]
    await persist_rule_verdicts(
        db_session,
        run_id=run_id,
        results_by_local_id={
            "QDraft_MS1": [
                fail("hmo.claims.datatype", "claims", "bad"),
                RuleResult("hmo.source_uri.present", "pass"),
            ],
        },
    )
    row = (
        await db_session.execute(
            select(HmoStudioItemOverride).where(
                HmoStudioItemOverride.run_id == run_id,
                HmoStudioItemOverride.local_id == "QDraft_MS1",
            )
        )
    ).scalar_one()
    verdict = dict(row.rule_verdict)
    verdict["results"] = list(verdict["results"]) + [{
        "rule_id": "hmo.wikidata.label_candidates",
        "state": "error",
        "message": "probe budget exhausted for this run — check did not execute",
    }]
    verdict["overall"] = "error"
    verdict["error_count"] = 1
    row.rule_verdict = verdict
    await db_session.commit()

    written = await merge_api_rule_verdicts(
        db_session,
        run_id=run_id,
        results_by_local_id={
            "QDraft_MS1": [
                RuleResult("hmo.wikidata.label_candidates", "pass"),
                not_relevant("hmo.live.alive", "item is not on the wiki yet"),
            ],
        },
    )
    assert written == 1
    await db_session.refresh(row)
    merged = row.rule_verdict
    entries = {e["rule_id"]: e for e in merged["results"]}
    assert entries["hmo.wikidata.label_candidates"]["state"] == "pass"
    assert "probe budget" not in str(entries["hmo.wikidata.label_candidates"])
    assert entries["hmo.live.alive"]["state"] == "not_relevant"
    assert entries["hmo.claims.datatype"]["state"] == "fail"
    assert entries["hmo.source_uri.present"]["state"] == "pass"
    assert merged["error_count"] == 0
    assert merged["fail_count"] == 1
    assert merged["overall"] == "fail"
