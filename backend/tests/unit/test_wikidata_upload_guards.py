"""Regression guards for the Wikidata Studio upload path.

These pin the fixes for the 2026-04 duplicate/non-notability failure mode
(the subject of the 2026-06-07 bulk-deletion request). They are the
counterpart to ``test_item_validator.py`` (which pins the moat content);
here we pin that the moat is actually ENFORCED in the upload path and that
reconciliation fails CLOSED.

Guarantees pinned:
  - manuscripts reconcile by P3959 (NNL id), not the never-matching P8189;
  - a SPARQL lookup that cannot be completed BLOCKS creation (never minted
    as "absent → create");
  - an ERROR-severity validator issue BLOCKS the write (create or update);
  - a blocked item NEVER reaches ``uploader.upload_item``;
  - the conflict-checked person path is the one the upload uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from app.pipeline import wikidata_upload as wu
from converter.wikidata.reconciler import (
    ReconciliationUnavailableError,
    WikidataReconciler,
    WorkIdentityUncertainError,
    prewarm_reconciler_for_items,
)
from converter.wikidata.item_models import WikidataItem, WikidataStatement

# ── Stub item / statement types (mirror item_builder's WikidataItem shape) ──


@dataclass
class _Stmt:
    property_id: str
    value: Any = ""
    value_type: str = "external-id"
    qualifiers: list = field(default_factory=list)
    references: list = field(default_factory=list)


@dataclass
class _Item:
    entity_type: str
    labels: dict
    statements: list = field(default_factory=list)
    existing_qid: str = ""
    local_id: str = ""


def _manuscript(nnl: str | None = "990001234", *, label: str = "Jerusalem, NLI, MS 1234",
                shelfmark: str | None = None, qid: str = "") -> _Item:
    stmts: list[_Stmt] = []
    if nnl:
        stmts.append(_Stmt("P3959", nnl))
    if shelfmark:
        stmts.append(_Stmt("P217", shelfmark, value_type="string"))
    return _Item(entity_type="manuscript", labels={"en": label},
                 statements=stmts, existing_qid=qid, local_id=nnl or "ms")


def _person(*, viaf: str | None = "111", nli: str | None = "9870555",
            label: str = "ישראל ישראלי", qid: str = "") -> _Item:
    stmts: list[_Stmt] = []
    if viaf:
        stmts.append(_Stmt("P214", viaf))
    if nli:
        stmts.append(_Stmt("P8189", nli))
    return _Item(entity_type="person", labels={"he": label},
                 statements=stmts, existing_qid=qid, local_id=label)


# ── Fakes ───────────────────────────────────────────────────────────────


class _FakeReconciler:
    def __init__(self, *, ms_map: dict | None = None, person_map: dict | None = None,
                 work_map: dict | None = None, raise_on: str | None = None) -> None:
        self.ms_map = ms_map or {}
        self.person_map = person_map or {}
        self.work_map = work_map or {}
        self.raise_on = raise_on
        self.ms_calls: list = []
        self.person_calls: list = []
        self.work_calls: list = []

    def reconcile_manuscript_by_identifiers(self, nnl_id, shelfmark):
        self.ms_calls.append((nnl_id, shelfmark))
        if self.raise_on == "manuscript":
            raise ReconciliationUnavailableError("WDQS 429")
        return self.ms_map.get(nnl_id)

    def reconcile_person_by_identifiers(self, viaf_id, nli_id, lc_id=None,
                                        gnd_id=None, isni=None):
        self.person_calls.append((viaf_id, nli_id, lc_id, gnd_id, isni))
        if self.raise_on == "person":
            raise ReconciliationUnavailableError("timeout")
        for k in (viaf_id, nli_id, lc_id, gnd_id, isni):
            if k and k in self.person_map:
                return self.person_map[k]
        return None

    def reconcile_work_by_label_and_author(self, title, lang="he", author_qid=None):
        self.work_calls.append((title, lang, author_qid))
        if self.raise_on == "work":
            raise ReconciliationUnavailableError("work outage")
        return self.work_map.get(title)


@dataclass
class _FakeResult:
    qid: str
    status: str
    message: str
    added_properties: list = field(default_factory=list)


# ── _prepare_for_upload: reconciliation + validation gate ─────────────────


def test_manuscript_reconciles_by_p3959_then_takes_update_semantics():
    item = _manuscript("990001234")
    rec = _FakeReconciler(ms_map={"990001234": "Q555"})

    prepared = wu._prepare_for_upload([item], rec)

    assert rec.ms_calls == [("990001234", None)]
    assert prepared[0].existing_qid == "Q555"
    assert prepared[0].blocked is False
    # mutated in place so the uploader takes UPDATE (not CREATE) semantics
    assert item.existing_qid == "Q555"


def test_manuscript_no_match_is_eligible_for_create():
    item = _manuscript("990009999")
    rec = _FakeReconciler(ms_map={})  # confirmed absent

    prepared = wu._prepare_for_upload([item], rec)

    assert prepared[0].existing_qid is None
    assert prepared[0].blocked is False


def test_manuscript_shelfmark_is_passed_through_for_fallback():
    item = _manuscript(nnl=None, shelfmark="MS 99")
    rec = _FakeReconciler()

    wu._prepare_for_upload([item], rec)

    assert rec.ms_calls == [(None, "MS 99")]


def test_sparql_outage_blocks_creation_fail_closed():
    item = _manuscript("990001234")
    rec = _FakeReconciler(raise_on="manuscript")

    prepared = wu._prepare_for_upload([item], rec)

    assert prepared[0].blocked is True
    assert prepared[0].block_status == "blocked"
    assert prepared[0].existing_qid is None
    assert "Query Service" in prepared[0].block_message


def test_person_uses_conflict_checked_identifier_path():
    item = _person(viaf="111", nli="9870555")
    rec = _FakeReconciler(person_map={"111": "Q900"})

    prepared = wu._prepare_for_upload([item], rec)

    assert rec.person_calls == [("111", "9870555", None, None, None)]
    assert prepared[0].existing_qid == "Q900"
    assert prepared[0].blocked is False


def test_validator_skips_person_with_no_identifier():
    # Identifierless persons are omitted/skipped (W-154/W-185), not validator-blocked.
    item = _Item(entity_type="person", labels={"en": "Winter"},
                 statements=[], existing_qid="", local_id="winter")
    rec = _FakeReconciler()

    prepared = wu._prepare_for_upload([item], rec)

    assert prepared[0].blocked is False
    assert prepared[0].block_status == "skipped"
    assert "W-154" in prepared[0].block_message or "publishable" in prepared[0].block_message.lower()


def test_validator_blocks_p50_on_manuscript_even_when_matched():
    # An error-severity issue blocks even an UPDATE to an item we matched.
    item = _manuscript("990001234")
    item.statements.append(_Stmt("P50", "Q42", value_type="item"))
    rec = _FakeReconciler(ms_map={"990001234": "Q555"})

    prepared = wu._prepare_for_upload([item], rec)

    assert prepared[0].blocked is True
    assert "P50_ON_MANUSCRIPT" in prepared[0].block_message


def test_work_reconciles_by_label_and_author():
    work = _Item(entity_type="work", labels={"he": "פירוש רש\"י"},
                 statements=[_Stmt("P50", "Q42", value_type="item")],
                 existing_qid="", local_id="rashi")
    rec = _FakeReconciler(work_map={"פירוש רש\"י": "Q300"})

    prepared = wu._prepare_for_upload([work], rec)

    assert rec.work_calls == [("פירוש רש\"י", "he", "Q42")]
    assert prepared[0].existing_qid == "Q300"
    assert prepared[0].blocked is False


@pytest.mark.parametrize("path", ["direct", "web", "prewarm", "bridge", "prebuilt", "ledger"])
def test_cantarini_does_not_adopt_the_mixed_work(monkeypatch, path: str) -> None:
    name = "קנטריני, יצחק חיים"

    def binding(qid: str, prop: str, value: str) -> dict[str, dict[str, str]]:
        return {"item": {"value": f"http://www.wikidata.org/entity/{qid}"},
                "title": {"value": "דרושים"}, prop: {"value": value}}

    bindings = [binding("Q141175543", "authorStr", name),
                binding("Q141175543", "authorStr", "אברהם"),
                binding("Q141755239", "author", "http://www.wikidata.org/entity/Q6076130")]
    reconciler = WikidataReconciler()
    calls: list[str] = []

    def query(sparql: str) -> list[dict[str, dict[str, str]]]:
        calls.append(sparql)
        return bindings

    monkeypatch.setattr(reconciler, "_query", query)
    work = WikidataItem(
        entity_type="work", local_id="work:cantarini", labels={"he": "דרושים"},
        records=["990001915930205171"], statements=[
            WikidataStatement("P31", "Q47461344", "item"),
            WikidataStatement("P1476", "דרושים", "monolingualtext"),
            WikidataStatement("P50", "Q6076130", "item"),
        ], work_candidate_evidence=[{
            "accepted": True, "title": "דרושים", "source_field": "100/245", "author_name": name,
        }],
    )
    if path == "direct":
        assert reconciler.reconcile_work_by_label_and_author(
            "דרושים", author_qid="Q6076130", author_name=name,
        ) == "Q141755239"
        return
    if path == "prewarm":
        prewarm_reconciler_for_items(reconciler, [work])
    warmed = len(calls)
    if path == "bridge":
        work.statements.append(WikidataStatement(
            "P2888", "https://mhm-hmo.wikibase.cloud/wiki/Item:Q3398", "url",
        ))
        monkeypatch.setattr(reconciler, "reconcile_by_exact_match_uri", lambda uri: "Q141175543")
    if path == "prebuilt":
        work.existing_qid = "Q141175543"
    ledger = None
    if path == "ledger":
        from app.pipeline.wikidata_qid_ledger import ledger_key_for_item

        ledger = {ledger_key_for_item(work): "Q141175543"}
    prepared = wu._prepare_for_upload([work], reconciler, ledger=ledger)[0]
    assert prepared.blocked or prepared.existing_qid == "Q141755239"
    if path == "prewarm":
        assert len(calls) == warmed


def test_source_scoped_sermon_does_not_adopt_a_title_only_candidate(monkeypatch) -> None:
    reconciler = WikidataReconciler()
    calls: list[str] = []

    def query(sparql: str) -> list[dict[str, dict[str, str]]]:
        calls.append(sparql)
        return [{"item": {"value": "http://www.wikidata.org/entity/Q123"}}]

    monkeypatch.setattr(reconciler, "_query", query)
    work = WikidataItem(
        entity_type="work", local_id="work:sermon:source:990003",
        labels={"he": "דרשה על פרשת ויגש"}, records=["990003"],
        work_candidate_evidence=[{"accepted": True, "identity_scope": "record"}],
    )
    assert wu._reconcile_for_upload(reconciler, work, "work")[0] is None
    assert calls == []


@pytest.mark.parametrize("with_ledger", [False, True])
def test_source_scoped_preview_does_not_claim_duplicate_absence(monkeypatch, with_ledger) -> None:
    work = WikidataItem(
        entity_type="work", local_id="work:source", labels={"en": "Source work"},
        work_candidate_evidence=[{"identity_scope": "record"}],
    )
    monkeypatch.setattr(wu, "_make_reconciler", lambda: _FakeReconciler())
    monkeypatch.setattr(
        "converter.wikidata.reconciler.prewarm_reconciler_for_items", lambda *a: None,
    )
    outcomes = (
        wu._reconcile_sync_with_ledger([work], {}) if with_ledger else wu._reconcile_sync([work])
    )
    assert outcomes[0].method == "source_scoped_work"
    assert "no conclusive duplicate check" in outcomes[0].message.lower()


@pytest.mark.parametrize("is_test", [False, True])
@pytest.mark.parametrize("has_bridge", [False, True])
def test_source_scoped_absence_never_creates_on_live(monkeypatch, is_test, has_bridge) -> None:
    work = WikidataItem(
        entity_type="work", local_id="work:source", labels={"en": "A source-scoped work"},
        records=["990001234567805171"],
        work_candidate_evidence=[{"accepted": True, "identity_scope": "record"}],
        statements=[WikidataStatement("P2888", "https://example.org/work", "url")]
        if has_bridge else [],
    )

    class Reconciler(_FakeReconciler):
        def reconcile_by_exact_match_uri(self, uri):
            return None

    monkeypatch.setattr(wu, "_make_reconciler", lambda: Reconciler())
    monkeypatch.setattr(
        "app.pipeline.wikidata_export_quality_gate.wikidata_export_quality_findings",
        lambda *a, **kw: [],
    )
    writes = []
    uploader = SimpleNamespace(
        upload_item=lambda item, **kw: writes.append(item) or _FakeResult("Q123", "success", "ok"),
    )
    outcome = wu._upload_sync(
        [work], "", False, {}, "wikidata", is_test=is_test, allow_live=True, uploader=uploader,
    )[0]
    assert outcome.status == ("created" if is_test else "blocked")
    assert len(writes) == int(is_test)
    if not is_test:
        assert "duplicate" in outcome.message.lower()
        assert work.existing_qid is None


def test_non_scoped_work_with_completed_absence_check_can_create(monkeypatch) -> None:
    work = WikidataItem(
        entity_type="work", local_id="work:ordinary", labels={"en": "A work"},
        statements=[WikidataStatement("P50", "Q42", "item")],
    )
    monkeypatch.setattr(wu, "_make_reconciler", lambda: _FakeReconciler())
    monkeypatch.setattr(
        "app.pipeline.wikidata_export_quality_gate.wikidata_export_quality_findings",
        lambda *a, **kw: [],
    )
    writes = []
    uploader = SimpleNamespace(
        upload_item=lambda item, **kw: writes.append(item) or _FakeResult("Q123", "success", "ok"),
    )
    outcome = wu._upload_sync(
        [work], "", False, {}, "wikidata", is_test=False, allow_live=True, uploader=uploader,
    )[0]
    assert outcome.status == "created"
    assert writes == [work]


@pytest.mark.parametrize("path", ["bridge", "prebuilt", "ledger"])
def test_source_scoped_verified_target_still_updates(monkeypatch, path) -> None:
    from app.pipeline.wikidata_qid_ledger import ledger_key_for_item

    work = WikidataItem(
        entity_type="work", local_id="work:source", labels={"en": "Source work"},
        records=["990001234567805171"],
        work_candidate_evidence=[{"accepted": True, "identity_scope": "record"}],
        statements=[WikidataStatement("P2888", "https://example.org/work", "url")]
        if path in {"bridge", "prebuilt"} else [],
        existing_qid="Q123" if path == "prebuilt" else None,
    )

    class Reconciler(_FakeReconciler):
        def reconcile_by_exact_match_uri(self, uri):
            return "Q123"

    monkeypatch.setattr(wu, "_make_reconciler", lambda: Reconciler())
    monkeypatch.setattr(
        "app.pipeline.wikidata_export_quality_gate.wikidata_export_quality_findings",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr("app.pipeline.wikidata_existence.confirm_qid_alive", lambda *a, **kw: True)
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_p31", lambda *a, **kw: {"Q123": []},
    )
    monkeypatch.setattr("app.pipeline.wikidata_existence.fetch_entity_labels", lambda *a, **kw: {})
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_duplicate_metadata",
        lambda *a, **kw: {"Q123": {
            "source_records": ["990001234567805171"], "p2888": ["https://example.org/work"],
        }},
    )
    writes = []
    uploader = SimpleNamespace(
        _is_our_item=lambda qid: True,
        upload_item=lambda item, **kw: writes.append(item.existing_qid)
        or _FakeResult("Q123", "updated", "ok"),
    )
    ledger = {ledger_key_for_item(work): "Q123"} if path == "ledger" else {}
    outcome = wu._upload_sync(
        [work], "", False, ledger, "wikidata", is_test=False, allow_live=True, uploader=uploader,
    )[0]
    assert outcome.status in {"updated", "adopted"}
    assert writes == ["Q123"]


@pytest.mark.parametrize("strong", [False, True])
@pytest.mark.parametrize("proposed,existing,expected", [
    ({"author_qids": frozenset({"Q42"})}, {}, "uncertain"),
    ({"author_names": frozenset({"author"})}, {}, "uncertain"),
    ({"author_qids": frozenset({"Q42"})}, {"author_qids": frozenset({"Q42"})}, "match"),
    ({}, {}, "match"),
])
def test_work_identity_requires_evidence_for_proposed_authors(
    strong, proposed, existing, expected,
) -> None:
    from converter.wikidata.work_identity import WorkIdentity, compare_work_identity

    assert compare_work_identity(
        WorkIdentity(**proposed), WorkIdentity(**existing), strong_identifier=strong,
    ) == expected


def test_strong_work_identifier_allows_existing_author_without_proposing_one() -> None:
    from converter.wikidata.work_identity import WorkIdentity, compare_work_identity

    existing = WorkIdentity(author_qids=frozenset({"Q42"}))
    assert compare_work_identity(WorkIdentity(), existing, strong_identifier=True) == "match"
    assert compare_work_identity(WorkIdentity(), existing) == "uncertain"


def test_different_candidate_does_not_authorize_source_scoped_create(monkeypatch) -> None:
    from app.pipeline.wikidata_duplicate_confirm import DIFFERENT_ITEM

    work = WikidataItem(
        entity_type="work", local_id="work:source", labels={"en": "Source work"},
        existing_qid="Q123", records=["990001234567805171"],
        work_candidate_evidence=[{"accepted": True, "identity_scope": "record"}],
    )
    prepared = wu.PreparedItem(
        item=work, local_id=work.local_id, label="Source work", entity_type="work",
        existing_qid="Q123", method="prebuilt", blocked=False, block_status="", block_message="",
    )
    monkeypatch.setattr("app.pipeline.wikidata_existence.confirm_qid_alive", lambda *a, **kw: True)
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_p31", lambda *a, **kw: {"Q123": []},
    )
    monkeypatch.setattr("app.pipeline.wikidata_existence.fetch_entity_labels", lambda *a, **kw: {})
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_duplicate_metadata",
        lambda *a, **kw: {"Q123": {"source_records": ["990001234567805171"]}},
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_duplicate_confirm.confirm_uncertain_duplicate",
        lambda **kw: DIFFERENT_ITEM,
    )
    result = wu._apply_existence_and_ownership(
        prepared, accept=None, ownership_checker=None, is_test=False,
    )
    assert result.blocked
    assert result.existing_qid == "Q123"
    assert work.existing_qid == "Q123"
    assert "duplicate" in result.block_message.lower()


def test_ambiguous_exact_match_uri_does_not_mean_absence(monkeypatch) -> None:
    reconciler = WikidataReconciler()
    monkeypatch.setattr(reconciler, "_query", lambda query: [
        {"item": {"value": "http://www.wikidata.org/entity/Q123"}},
        {"item": {"value": "http://www.wikidata.org/entity/Q124"}},
    ])
    with pytest.raises(WorkIdentityUncertainError):
        reconciler.reconcile_by_exact_match_uri("https://example.org/work")


def test_live_work_gate_checks_authors_before_an_owned_ledger_bypass(monkeypatch) -> None:
    work = WikidataItem(
        entity_type="work", local_id="work:cantarini", existing_qid="Q141175543",
        statements=[WikidataStatement("P50", "Q6076130", "item", qualifiers=[{
            "property": "P1932", "value": "קנטריני, יצחק חיים", "type": "string",
        }])],
    )
    prepared = wu.PreparedItem(
        item=work, local_id=work.local_id, label="דרושים", entity_type="work",
        existing_qid=work.existing_qid, method="ledger", blocked=False,
        block_status="", block_message="",
    )
    monkeypatch.setattr("app.pipeline.wikidata_existence.confirm_qid_alive",
                        lambda *args, **kwargs: True)
    monkeypatch.setattr("app.pipeline.wikidata_existence.fetch_entity_p31",
                        lambda *args, **kwargs: {"Q141175543": ["Q47461344"]})
    monkeypatch.setattr("app.pipeline.wikidata_existence.fetch_entity_duplicate_metadata",
                        lambda *args, **kwargs: {"Q141175543": {
                            "authors": ["קנטריני, יצחק חיים", "אברהם"], "p2888": [],
                        }})
    result = wu._apply_existence_and_ownership(
        prepared, accept=None, ownership_checker=None, is_test=False,
    )
    assert result.blocked
    assert result.method == "work_identity_review"
    assert result.link_qid is None


@pytest.mark.parametrize("scope", ["author", "source"])
@pytest.mark.parametrize("failure", ["fetch", "missing", "empty", "malformed", "parse"])
def test_live_work_gate_blocks_unavailable_identity_metadata(monkeypatch, scope, failure) -> None:
    work = WikidataItem(
        entity_type="work", local_id="work:1", existing_qid="Q123",
        statements=[WikidataStatement("P50", "Q42", "item")] if scope == "author" else [],
        records=["990001234567805171"],
        work_candidate_evidence=[{"identity_scope": "record"}] if scope == "source" else [],
    )
    prepared = wu.PreparedItem(
        item=work, local_id=work.local_id, label="Work", entity_type="work",
        existing_qid="Q123", method="ledger", blocked=False,
        block_status="", block_message="", adopt_candidate=True,
    )
    monkeypatch.setattr("app.pipeline.wikidata_existence.confirm_qid_alive", lambda *a, **kw: True)
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_p31", lambda *a, **kw: {"Q123": []},
    )

    def fetch(*args, **kwargs):
        if failure == "fetch":
            raise RuntimeError("metadata unavailable")
        if failure == "missing":
            return {}
        if failure == "empty":
            return {"Q123": {}}
        if failure == "malformed":
            return {"Q123": {"author_qids": [42]}}
        return {"Q123": {"authors": []}}

    monkeypatch.setattr("app.pipeline.wikidata_existence.fetch_entity_duplicate_metadata", fetch)
    if failure == "parse":
        def invalid_metadata(_metadata):
            raise ValueError("invalid metadata")

        monkeypatch.setattr(
            "converter.wikidata.work_identity.work_identity_from_metadata", invalid_metadata,
        )

    result = wu._apply_existence_and_ownership(
        prepared, accept=wu.ForeignAccept(True, "Q123"),
        ownership_checker=SimpleNamespace(_is_our_item=lambda qid: True), is_test=False,
    )

    assert result.blocked
    assert result.block_status == "blocked"
    assert result.method == "work_identity_review"
    assert "unverified" in result.block_message
    assert result.link_qid is None


@pytest.mark.parametrize("scenario", [
    "matching", "matching_source", "empty_authors", "no_comparison", "test_wiki",
])
def test_work_identity_gate_preserves_valid_paths(monkeypatch, scenario) -> None:
    work = WikidataItem(
        entity_type="work", local_id="work:1", existing_qid="Q123",
        statements=([] if scenario in {"no_comparison", "matching_source"}
                    else [WikidataStatement("P50", "Q42", "item")]),
        records=["990001234567805171"],
        work_candidate_evidence=(
            [{"identity_scope": "record"}] if scenario == "matching_source" else []
        ),
    )
    prepared = wu.PreparedItem(
        item=work, local_id=work.local_id, label="Work", entity_type="work",
        existing_qid="Q123", method="prebuilt", blocked=False,
        block_status="", block_message="",
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_p31", lambda *a, **kw: {"Q123": []},
    )
    calls = []

    def fetch(*args, **kwargs):
        calls.append(True)
        return {"Q123": {
            "author_qids": ["Q42"] if scenario == "matching" else [],
            "source_records": ["990001234567805171"] if scenario == "matching_source" else [],
        }}

    monkeypatch.setattr("app.pipeline.wikidata_existence.fetch_entity_duplicate_metadata", fetch)
    result = wu._apply_work_identity_gate(prepared, qid="Q123", is_test=scenario == "test_wiki")

    assert result.blocked is (scenario == "empty_authors")
    assert result.existing_qid == "Q123"
    assert bool(calls) == (scenario in {"matching", "matching_source", "empty_authors"})


@pytest.mark.parametrize("missing", ["p31", "author_metadata"])
def test_upload_does_not_write_work_when_identity_metadata_is_unavailable(
    monkeypatch, missing,
) -> None:
    work = WikidataItem(
        entity_type="work", local_id="work:1", existing_qid="Q123",
        labels={"en": "Work"}, statements=[WikidataStatement("P50", "Q42", "item")],
    )
    monkeypatch.setattr(wu, "_make_reconciler", lambda: _FakeReconciler())
    monkeypatch.setattr(
        "app.pipeline.wikidata_export_quality_gate.wikidata_export_quality_findings",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr("app.pipeline.wikidata_existence.confirm_qid_alive", lambda *a, **kw: True)
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_p31",
        lambda *a, **kw: {} if missing == "p31" else {"Q123": []},
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_duplicate_metadata", lambda *a, **kw: {},
    )
    writes = []
    uploader = SimpleNamespace(
        _is_our_item=lambda qid: True,
        upload_item=lambda item, **kwargs: writes.append(item),
    )

    outcomes = wu._upload_sync(
        [work], token="", dry_run=False, ledger={}, ledger_ns="wikidata",
        is_test=False, allow_live=True, uploader=uploader,
    )

    assert outcomes[0].status == "blocked"
    assert writes == []


@pytest.mark.parametrize("is_test", [False, True])
@pytest.mark.parametrize("failure", ["missing", "exception"])
def test_work_gate_blocks_unknown_live_p31_but_preserves_test_behavior(
    monkeypatch, is_test, failure,
) -> None:
    work = WikidataItem(entity_type="work", local_id="work:1", existing_qid="Q123")
    prepared = wu.PreparedItem(
        item=work, local_id=work.local_id, label="Work", entity_type="work",
        existing_qid="Q123", method="prebuilt", blocked=False,
        block_status="", block_message="",
    )

    def fetch(*args, **kwargs):
        if failure == "exception":
            raise ValueError("invalid P31")
        return {}

    monkeypatch.setattr("app.pipeline.wikidata_existence.fetch_entity_p31", fetch)
    result = wu._apply_work_identity_gate(prepared, qid="Q123", is_test=is_test)
    assert result.blocked is not is_test
    if not is_test:
        assert result.method == "work_identity_review"
        assert "unverified P31" in result.block_message
        assert result.link_qid is None


@pytest.mark.parametrize("claims", [None, "absent", {}, []])
def test_work_gate_distinguishes_missing_claims_from_empty_claims(monkeypatch, claims) -> None:
    from app.pipeline import wikidata_existence as existence

    entity = {"id": "Q123", "descriptions": {}}
    if claims != "absent":
        entity["claims"] = claims
    monkeypatch.setattr(existence, "_PREFETCHED_ENTITIES", {})
    monkeypatch.setattr(
        existence, "_fetch_json_throttled", lambda *a, **kw: {"entities": {"Q123": entity}},
    )
    work = WikidataItem(
        entity_type="work", local_id="work:1", existing_qid="Q123",
        statements=[WikidataStatement("P50", "Q42", "item")],
    )
    prepared = wu.PreparedItem(
        item=work, local_id=work.local_id, label="Work", entity_type="work",
        existing_qid="Q123", method="ledger", blocked=False,
        block_status="", block_message="",
    )
    result = wu._apply_work_identity_gate(prepared, qid="Q123", is_test=False)
    assert result.blocked
    if claims is None or claims == "absent":
        assert "P31" in result.block_message
    else:
        assert "author/source" in result.block_message


@pytest.mark.parametrize("missing_response", ["p31", "metadata"])
def test_work_gate_blocks_when_only_one_fetch_has_claims(monkeypatch, missing_response) -> None:
    from urllib.parse import parse_qs, urlsplit

    from app.pipeline import wikidata_existence as existence

    def fetch(url, **kwargs):
        props = parse_qs(urlsplit(url).query)["props"][0]
        entity = {"id": "Q123", "descriptions": {}}
        if (props == "claims") != (missing_response == "p31"):
            entity["claims"] = {}
        return {"entities": {"Q123": entity}}

    monkeypatch.setattr(existence, "_PREFETCHED_ENTITIES", {})
    monkeypatch.setattr(existence, "_fetch_json_throttled", fetch)
    work = WikidataItem(
        entity_type="work", local_id="work:1", existing_qid="Q123",
        statements=[WikidataStatement("P50", "Q42", "item")],
    )
    prepared = wu.PreparedItem(
        item=work, local_id=work.local_id, label="Work", entity_type="work",
        existing_qid="Q123", method="prebuilt", blocked=False,
        block_status="", block_message="",
    )
    result = wu._apply_work_identity_gate(prepared, qid="Q123", is_test=False)
    assert result.blocked
    assert result.method == "work_identity_review"
    assert "unverified" in result.block_message


@pytest.mark.parametrize("method", ["prebuilt", "ledger"])
@pytest.mark.parametrize("author_snak", [
    {"snaktype": "somevalue"},
    {"snaktype": "novalue"},
    {"snaktype": "value", "datavalue": {"value": {"id": "invalid"}}},
    {},
])
def test_strong_work_identity_blocks_unresolved_live_authors(
    monkeypatch, method, author_snak,
) -> None:
    from app.pipeline import wikidata_existence as existence

    entity = {"id": "Q123", "descriptions": {}, "claims": {"P50": [{"mainsnak": author_snak}]}}
    monkeypatch.setattr(existence, "_PREFETCHED_ENTITIES", {(False, "Q123"): entity})
    work = WikidataItem(
        entity_type="work", local_id="work:1", existing_qid="Q123",
        statements=[WikidataStatement("P50", "Q42", "item")],
    )
    prepared = wu.PreparedItem(
        item=work, local_id=work.local_id, label="Work", entity_type="work",
        existing_qid="Q123", method=method, blocked=False,
        block_status="", block_message="",
    )
    result = wu._apply_work_identity_gate(prepared, qid="Q123", is_test=False)
    assert result.blocked
    assert result.method == "work_identity_review"


@pytest.mark.parametrize("accepted_qid", [None, "Q999", "Q123"])
def test_foreign_work_requires_accept_for_the_exact_qid(monkeypatch, accepted_qid) -> None:
    monkeypatch.setattr("app.pipeline.wikidata_existence.confirm_qid_alive", lambda *a, **kw: True)
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_p31", lambda *a, **kw: {"Q123": []},
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.fetch_entity_duplicate_metadata",
        lambda *a, **kw: {"Q123": {"author_qids": ["Q42"]}},
    )
    work = WikidataItem(
        entity_type="work", local_id="work:1", existing_qid="Q123",
        statements=[WikidataStatement("P50", "Q42", "item")],
    )
    prepared = wu.PreparedItem(
        item=work, local_id=work.local_id, label="Work", entity_type="work",
        existing_qid="Q123", method="identifier", blocked=False,
        block_status="", block_message="",
    )
    result = wu._apply_existence_and_ownership(
        prepared,
        accept=wu.ForeignAccept(accept_foreign_modify=True, accepted_foreign_qid=accepted_qid),
        ownership_checker=SimpleNamespace(_is_our_item=lambda qid: False),
        is_test=False,
    )
    assert result.blocked is (accepted_qid != "Q123")
    assert result.allow_foreign_modify is (accepted_qid == "Q123")


@pytest.mark.parametrize("accepted_qid", [None, "Q999", "Q123"])
def test_p3959_is_not_foreign_ownership(monkeypatch, accepted_qid) -> None:
    monkeypatch.setattr("app.pipeline.wikidata_existence.confirm_qid_alive", lambda *a, **kw: True)
    seen = []

    class Checker:
        def _is_our_item(self, qid, *, our_p3959=None):
            seen.append(our_p3959)
            return bool(our_p3959)

    item = _manuscript("990000533850205171", qid="Q123")
    prepared = wu.PreparedItem(
        item=item, local_id=item.local_id, label="MS", entity_type="manuscript",
        existing_qid="Q123", method="p3959/shelfmark", blocked=False,
        block_status="", block_message="",
    )
    result = wu._apply_existence_and_ownership(
        prepared, accept=wu.ForeignAccept(True, accepted_qid),
        ownership_checker=Checker(), is_test=False,
    )
    assert result.ownership == "foreign"
    assert result.blocked is (accepted_qid != "Q123")
    assert result.allow_foreign_modify is (accepted_qid == "Q123")
    assert seen == [None]


@pytest.mark.parametrize("target_qid,approved", [("Q123", True), ("Q123", False), ("Q999", True)])
def test_foreign_pass2_requires_the_same_item_and_qid_approval(
    monkeypatch, target_qid, approved,
) -> None:
    monkeypatch.setattr(wu, "_make_reconciler", lambda: _FakeReconciler())
    monkeypatch.setattr(
        "app.pipeline.wikidata_export_quality_gate.wikidata_export_quality_findings",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr("app.pipeline.wikidata_existence.confirm_qid_alive", lambda *a, **kw: True)

    class Uploader:
        def __init__(self):
            self._foreign_accept_qids = set()
            self.writes = []

        def _is_our_item(self, qid):
            return qid in self._foreign_accept_qids

        def register_foreign_accept(self, qid):
            self._foreign_accept_qids.add(qid)

        def upload_item(self, item, **kwargs):
            self.writes.append(item.existing_qid)
            return _FakeResult(qid=item.existing_qid, status="updated", message="updated")

    uploader = Uploader()
    first = _manuscript("990000533850205171", qid="Q123")
    accepts = {first.local_id: wu.ForeignAccept(True, "Q123")}
    outcome = wu._upload_sync(
        [first], "", False, {}, "wikidata", accept_by_local_id=accepts,
        is_test=False, allow_live=True, uploader=uploader,
        check_foreign_accept=lambda local_id, qid: True,
    )[0]
    assert outcome.ownership == "foreign"
    assert outcome.accepted_foreign_qid == "Q123"
    assert wu.pass2_may_update_source(outcome, "Q123", accept=accepts[first.local_id])

    second = _manuscript("990000533850205171", qid=target_qid)
    second_accepts = accepts if approved else {}
    second_outcome = wu._upload_sync(
        [second], "", False, {}, "wikidata", accept_by_local_id=second_accepts,
        is_test=False, allow_live=True, uploader=uploader,
        check_foreign_accept=lambda local_id, qid: True,
    )[0]
    assert second_outcome.status == ("updated" if approved and target_qid == "Q123" else "skipped")
    assert uploader.writes == (["Q123", "Q123"] if approved and target_qid == "Q123" else ["Q123"])


@pytest.mark.parametrize("status,ownership,qid,accepted,allowed", [
    ("updated", "foreign", "Q123", "Q123", True),
    ("exists", "foreign", "Q123", "Q123", True),
    ("updated", "foreign", "Q123", "Q999", False),
    ("updated", "foreign", "Q123", None, False),
    ("updated", "foreign", None, None, False),
    ("success", "foreign", "Q123", None, False),
    ("created", "absent", "Q123", None, True),
    ("updated", "own", "Q123", None, True),
    ("skipped", "foreign", "Q123", "Q123", False),
])
def test_pass2_requires_authorized_outcome(status, ownership, qid, accepted, allowed) -> None:
    outcome = wu.UploadOutcome(
        local_id="ms", label="MS", entity_type="manuscript", qid=qid, status=status,
        message="", added_properties=[], ownership=ownership, accepted_foreign_qid=accepted,
    )
    assert wu.pass2_may_update_source(
        outcome, qid, accept=wu.ForeignAccept(True, accepted),
    ) is allowed


@pytest.mark.parametrize("status,ownership,qid,accepted,target,dry_run,allowed", [
    ("created", "absent", "Q123", None, "Q123", False, True),
    ("created", "absent", "Q123", None, "Q999", False, False),
    ("updated", "own", "Q123", None, "Q999", False, False),
    ("updated", "foreign", "Q123", "Q123", "Q123", False, True),
    ("updated", "foreign", "Q123", "Q123", "Q999", False, False),
    ("updated", "foreign", "Q123", "Q999", "Q123", False, False),
    ("updated", "own", "Q123", None, None, False, False),
    ("created", "absent", None, None, "Q123", False, False),
    ("created", "absent", "dry:ms", None, "dry:ms", False, False),
    ("updated", "own", "Q0", None, "Q0", False, False),
    ("success", "absent", None, None, "dry:ms", True, True),
    ("success", "absent", None, None, "dry:other", True, False),
    ("success", "absent", None, None, "dry:ms", False, False),
])
def test_pass2_binds_actual_target_to_first_pass(
    status, ownership, qid, accepted, target, dry_run, allowed,
) -> None:
    outcome = wu.UploadOutcome(
        local_id="ms", label="MS", entity_type="manuscript", qid=qid, status=status,
        message="", added_properties=[], ownership=ownership, accepted_foreign_qid=accepted,
    )
    assert wu.pass2_may_update_source(
        outcome, target, accept=wu.ForeignAccept(True, accepted), dry_run=dry_run,
    ) is allowed
    assert not wu.pass2_may_update_source(None, target, dry_run=dry_run)


@pytest.mark.parametrize("accept", [
    None, wu.ForeignAccept(False, "Q123"), wu.ForeignAccept(True, "Q999"),
])
def test_pass2_rechecks_current_foreign_approval(accept) -> None:
    outcome = wu.UploadOutcome(
        local_id="ms", label="MS", entity_type="manuscript", qid="Q123", status="updated",
        message="", added_properties=[], ownership="foreign", accepted_foreign_qid="Q123",
    )
    assert not wu.pass2_may_update_source(outcome, "Q123", accept=accept)


def test_foreign_outcome_does_not_authorize_a_different_returned_qid(monkeypatch) -> None:
    monkeypatch.setattr(wu, "_make_reconciler", lambda: _FakeReconciler())
    monkeypatch.setattr(
        "app.pipeline.wikidata_export_quality_gate.wikidata_export_quality_findings",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr("app.pipeline.wikidata_existence.confirm_qid_alive", lambda *a, **kw: True)
    uploader = SimpleNamespace(
        _is_our_item=lambda qid: False,
        register_foreign_accept=lambda qid: None,
        upload_item=lambda item, **kwargs: _FakeResult("Q999", "updated", "unexpected target"),
    )
    item = _manuscript("990000533850205171", qid="Q123")
    outcome = wu._upload_sync(
        [item], "", False, {}, "wikidata",
        accept_by_local_id={item.local_id: wu.ForeignAccept(True, "Q123")},
        is_test=False, allow_live=True, uploader=uploader,
        check_foreign_accept=lambda local_id, qid: True,
    )[0]
    assert outcome.qid == "Q999"
    assert outcome.accepted_foreign_qid is None
    assert not wu.pass2_may_update_source(outcome, "Q999")


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [
    "valid", "revoked", "changed", "other_local", "deleted", "db_error", "no_run", "own",
])
async def test_live_foreign_write_rechecks_persisted_approval_after_prepare(
    sample_run, db_session, monkeypatch, state,
) -> None:
    import asyncio
    import threading

    from app.models.item_override import WikidataItemOverride

    item = _manuscript("990000533850205171", qid="Q123")
    row = WikidataItemOverride(
        run_id=sample_run["run_id"], local_id=item.local_id,
        accept_foreign_modify=True, accepted_foreign_qid="Q123",
    )
    db_session.add(row)
    await db_session.commit()
    monkeypatch.setattr(wu, "_make_reconciler", lambda: _FakeReconciler())
    monkeypatch.setattr(
        "app.pipeline.wikidata_export_quality_gate.wikidata_export_quality_findings",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr("app.pipeline.wikidata_existence.confirm_qid_alive", lambda *a, **kw: True)
    ready = threading.Event()
    proceed = threading.Event()
    original_prepare = wu._prepare_for_upload

    def prepare(*args, **kwargs):
        prepared = original_prepare(*args, **kwargs)
        ready.set()
        if not proceed.wait(10):
            raise TimeoutError("test did not resume preflight")
        return prepared

    monkeypatch.setattr(wu, "_prepare_for_upload", prepare)
    checks = []
    if state in {"db_error", "own"}:
        async def unavailable(*args):
            checks.append(True)
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(wu, "_persisted_foreign_accept", unavailable)

    writes = []
    registrations = []
    uploader = SimpleNamespace(
        _is_our_item=lambda qid: state == "own",
        register_foreign_accept=lambda qid: registrations.append(qid),
        upload_item=lambda item, **kw: (
            writes.append(item.existing_qid) or _FakeResult(item.existing_qid, "updated", "ok")
        ),
    )
    task = asyncio.create_task(wu.upload_items(
        [item], token="", mode=wu.resolve_upload_mode("live"),
        run_id=None if state == "no_run" else sample_run["run_id"],
        ledger={}, accept_by_local_id={item.local_id: wu.ForeignAccept(True, "Q123")},
        uploader=uploader,
    ))
    try:
        assert await asyncio.to_thread(ready.wait, 10)
        if state == "revoked":
            row.accept_foreign_modify = False
        elif state == "changed":
            row.accepted_foreign_qid = "Q999"
        elif state == "other_local":
            row.local_id = "different-item"
        elif state == "deleted":
            await db_session.delete(row)
        if state in {"revoked", "changed", "other_local", "deleted"}:
            await db_session.commit()
    finally:
        proceed.set()
    outcome = (await task)[0]
    allowed = state in {"valid", "own"}
    assert outcome.status == ("updated" if allowed else "blocked")
    assert writes == (["Q123"] if allowed else [])
    assert registrations == (["Q123"] if state == "valid" else [])
    if state == "own":
        assert outcome.ownership == "own"
        assert checks == []


@pytest.mark.parametrize("status,owns", [("updated", True), ("exists", True), ("updated", False)])
def test_created_candidate_adopted_as_owned_is_eligible_for_pass2(
    monkeypatch, status, owns,
) -> None:
    monkeypatch.setattr(wu, "_make_reconciler", lambda: _FakeReconciler())
    monkeypatch.setattr(
        "app.pipeline.wikidata_export_quality_gate.wikidata_export_quality_findings",
        lambda *a, **kw: [],
    )
    item = _manuscript("990000533850205171")
    uploader = SimpleNamespace(
        _is_our_item=lambda qid: owns,
        upload_item=lambda item, **kw: _FakeResult("Q123", status, "adopted"),
    )
    outcome = wu._upload_sync(
        [item], "", False, {}, "wikidata", is_test=False, allow_live=True, uploader=uploader,
    )[0]
    assert outcome.ownership == ("own" if owns else "foreign")
    assert outcome.status == ("adopted" if owns and status == "updated" else status)
    assert wu.pass2_may_update_source(outcome, "Q123") is owns


def test_changed_target_cannot_reuse_another_qid_authorization(monkeypatch) -> None:
    item = _manuscript("990000533850205171", qid="Q999")
    prepared = wu.PreparedItem(
        item=item, local_id=item.local_id, label="MS", entity_type="manuscript",
        existing_qid="Q123", method="ledger", blocked=False,
        block_status="", block_message="", ownership="own",
    )
    monkeypatch.setattr(wu, "_make_reconciler", lambda: _FakeReconciler())
    monkeypatch.setattr(wu, "_prepare_for_upload", lambda *a, **kw: [prepared])
    writes = []
    uploader = SimpleNamespace(upload_item=lambda *a, **kw: writes.append(True))

    outcome = wu._upload_sync(
        [item], "", False, {}, "wikidata", is_test=False, allow_live=True, uploader=uploader,
    )[0]
    assert outcome.status == "blocked"
    assert "target QID changed" in outcome.message
    assert writes == []


@pytest.mark.parametrize("failure", ["missing", "exception"])
def test_second_work_p31_lookup_failure_cannot_allow_create(monkeypatch, failure) -> None:
    from app.pipeline import wikidata_duplicate_confirm as confirm

    calls = []

    def fetch_p31(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            return {"Q123": ["Q47461344"]}
        if failure == "exception":
            raise ValueError("invalid P31")
        return {}

    monkeypatch.setattr("app.pipeline.wikidata_existence.confirm_qid_alive", lambda *a, **kw: True)
    monkeypatch.setattr("app.pipeline.wikidata_existence.fetch_entity_p31", fetch_p31)
    monkeypatch.setattr("app.pipeline.wikidata_existence.fetch_entity_labels", lambda *a, **kw: {})
    verdicts = []
    monkeypatch.setattr(
        confirm, "confirm_uncertain_duplicate",
        lambda **kw: verdicts.append(True) or confirm.DIFFERENT_ITEM,
    )
    work = WikidataItem(entity_type="work", local_id="work:1", existing_qid="Q123")
    prepared = wu.PreparedItem(
        item=work, local_id=work.local_id, label="Work", entity_type="work",
        existing_qid="Q123", method="label+author", blocked=False,
        block_status="", block_message="",
    )
    result = wu._apply_existence_and_ownership(
        prepared, accept=wu.ForeignAccept(), ownership_checker=None, is_test=False,
    )
    assert result.blocked
    assert result.existing_qid == "Q123"
    assert result.method == "work_identity_review"
    assert verdicts == []
    assert len(calls) == 2


def test_person_duplicate_confirmation_blocks_missing_p31(monkeypatch) -> None:
    from app.pipeline import wikidata_duplicate_confirm as confirm

    monkeypatch.setattr("app.pipeline.wikidata_existence.fetch_entity_p31", lambda *a, **kw: {})
    monkeypatch.setattr("app.pipeline.wikidata_existence.fetch_entity_labels", lambda *a, **kw: {})
    verdicts = []
    monkeypatch.setattr(
        confirm, "confirm_uncertain_duplicate",
        lambda **kw: verdicts.append(True) or confirm.DIFFERENT_ITEM,
    )
    person = _person(qid="Q123")
    prepared = wu.PreparedItem(
        item=person, local_id=person.local_id, label="Person", entity_type="person",
        existing_qid="Q123", method="prebuilt", blocked=False,
        block_status="", block_message="",
    )
    result = wu._apply_uncertain_duplicate_confirm(prepared, is_test=False)
    assert result.blocked
    assert result.existing_qid == "Q123"
    assert result.method == "identity_review"
    assert verdicts == []


def test_person_duplicate_confirmation_keeps_known_p31_with_unknown_snaks(monkeypatch) -> None:
    from app.pipeline import wikidata_duplicate_confirm as confirm
    from app.pipeline import wikidata_existence as existence

    entity = {"id": "Q123", "claims": {"P31": [
        {"mainsnak": {"snaktype": "somevalue"}},
        {"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": "Q5"}}}},
    ]}}
    monkeypatch.setattr(existence, "_PREFETCHED_ENTITIES", {(False, "Q123"): entity})
    monkeypatch.setattr(existence, "fetch_entity_labels", lambda *a, **kw: {})
    received = []
    monkeypatch.setattr(
        confirm, "confirm_uncertain_duplicate",
        lambda **kw: received.append(kw["live_p31"]) or confirm.SAME_ITEM,
    )
    person = _person(qid="Q123")
    prepared = wu.PreparedItem(
        item=person, local_id=person.local_id, label="Person", entity_type="person",
        existing_qid="Q123", method="prebuilt", blocked=False,
        block_status="", block_message="",
    )
    result = wu._apply_uncertain_duplicate_confirm(prepared, is_test=False)
    assert not result.blocked
    assert result.existing_qid == "Q123"
    assert received == [["Q5"]]


# ── _upload_sync: blocked items never reach the uploader ──────────────────


def test_upload_sync_wdqs_outage_creates_on_test(monkeypatch):
    """On test.wikidata.org, WDQS outage must not block CREATE (Rule W-181)."""
    monkeypatch.setenv("WIKIDATA_TEST_MODE", "true")
    monkeypatch.delenv("MORATORIUM_LIFTED", raising=False)
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
    )

    good = _manuscript("990000001")
    outage = _manuscript("990000002")

    class _SelectiveRec(_FakeReconciler):
        def reconcile_manuscript_by_identifiers(self, nnl_id, shelfmark):
            self.ms_calls.append((nnl_id, shelfmark))
            if nnl_id == "990000002":
                raise ReconciliationUnavailableError("timeout")
            return {"990000001": "Q1"}.get(nnl_id)

    monkeypatch.setattr(wu, "_make_reconciler", lambda: _SelectiveRec())

    written: list = []

    class _FakeUploader:
        def __init__(self, token, is_test, batch_mode, **_kwargs):
            assert is_test is True
            self._is_our_item_cache = {}

        def _is_our_item(self, qid: str) -> bool:
            return True

        def upload_item(self, item, **kwargs):
            written.append(item)
            qid = getattr(item, "existing_qid", None) or "Qnew"
            status = "updated" if getattr(item, "existing_qid", None) else "success"
            return _FakeResult(qid=qid, status=status, message=f"{status} {qid}")

    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", _FakeUploader)

    outcomes = wu._upload_sync(
        [good, outage], token="User@Bot:deadbeef", dry_run=False, ledger={}, ledger_ns="wikidata",
    )

    statuses = {o.local_id: o.status for o in outcomes}
    assert statuses["990000001"] == "adopted"
    assert statuses["990000002"] in {"created", "success"}
    assert len(written) == 2


def test_upload_sync_wdqs_outage_blocks_on_live(monkeypatch):
    monkeypatch.delenv("WIKIDATA_TEST_MODE", raising=False)
    monkeypatch.setenv("MORATORIUM_LIFTED", "true")
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
    )

    outage = _manuscript("990000002")

    class _Rec(_FakeReconciler):
        def reconcile_manuscript_by_identifiers(self, nnl_id, shelfmark):
            raise ReconciliationUnavailableError("timeout")

    monkeypatch.setattr(wu, "_make_reconciler", lambda: _Rec())

    written: list = []

    class _FakeUploader:
        def __init__(self, token, is_test, batch_mode, **_kwargs):
            self._is_our_item_cache = {}

        def _is_our_item(self, qid: str) -> bool:
            return True

        def upload_item(self, item, **kwargs):
            written.append(item)
            return _FakeResult(qid="Q1", status="success", message="ok")

    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", _FakeUploader)

    outcomes = wu._upload_sync(
        [outage],
        token="User@Bot:deadbeef",
        dry_run=False,
        ledger={},
        ledger_ns="wikidata",
        is_test=False,
        allow_live=True,
    )
    assert outcomes[0].status == "blocked"
    assert written == []


def test_missing_qid_on_test_clears_for_create(monkeypatch):
    item = _manuscript("990000099")
    item.existing_qid = "Q134603946"
    prepared = wu.PreparedItem(
        item=item,
        local_id="990000099",
        label="x",
        entity_type="manuscript",
        existing_qid="Q134603946",
        method="prebuilt",
        blocked=False,
        block_status="",
        block_message="",
    )
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: False,
    )
    out = wu._apply_existence_and_ownership(
        prepared,
        accept=None,
        ownership_checker=None,
        is_test=True,
    )
    assert out.blocked is False
    assert out.existing_qid is None
    assert item.existing_qid is None
    assert "cleared_missing_on_test" in out.method


def test_dry_run_reports_update_create_and_block(monkeypatch):
    update_item = _manuscript("990000010")
    create_item = _manuscript("990000011")
    block_item = _manuscript("990000012")
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
    )

    class _Rec(_FakeReconciler):
        def reconcile_manuscript_by_identifiers(self, nnl_id, shelfmark):
            if nnl_id == "990000012":
                raise ReconciliationUnavailableError("503")
            return {"990000010": "Q10"}.get(nnl_id)

    monkeypatch.setattr(wu, "_make_reconciler", lambda: _Rec())

    class _FakeUploader:
        def __init__(self, token, is_test, batch_mode, **_kwargs):
            self._is_our_item_cache = {}

        def _is_our_item(self, qid: str) -> bool:
            return True

    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", _FakeUploader)

    outcomes = wu._upload_sync(
        [update_item, create_item, block_item],
        token="User@Bot:deadbeef", dry_run=True,
        ledger={}, ledger_ns="wikidata",
    )
    by_id = {o.local_id: o for o in outcomes}

    assert by_id["990000010"].status == "exists"
    assert by_id["990000010"].qid == "Q10"
    assert "owned by your account" in by_id["990000010"].message
    assert by_id["990000011"].status == "success"
    assert by_id["990000012"].status == "blocked"


def test_dry_run_skips_foreign_without_accept(monkeypatch):
    item = _manuscript("990000020")
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
    )
    monkeypatch.setattr(
        wu, "_make_reconciler",
        lambda: _FakeReconciler(ms_map={"990000020": "Q20"}),
    )

    class _ForeignUploader:
        def __init__(self, token, is_test, batch_mode, **_kwargs):
            self._is_our_item_cache = {}

        def _is_our_item(self, qid: str) -> bool:
            return False

    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", _ForeignUploader)

    outcomes = wu._upload_sync(
        [item], token="User@Bot:deadbeef", dry_run=True,
        ledger={}, ledger_ns="wikidata",
    )
    assert outcomes[0].status == "skipped"
    assert "not created by your account" in outcomes[0].message


def test_dry_run_allows_foreign_with_accept(monkeypatch):
    item = _manuscript("990000021")
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
    )
    monkeypatch.setattr(
        wu, "_make_reconciler",
        lambda: _FakeReconciler(ms_map={"990000021": "Q21"}),
    )

    class _ForeignUploader:
        def __init__(self, token, is_test, batch_mode, **_kwargs):
            self._is_our_item_cache = {}

        def _is_our_item(self, qid: str) -> bool:
            return False

    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", _ForeignUploader)

    outcomes = wu._upload_sync(
        [item], token="User@Bot:deadbeef", dry_run=True,
        ledger={}, ledger_ns="wikidata",
        accept_by_local_id={
            "990000021": wu.ForeignAccept(
                accept_foreign_modify=True, accepted_foreign_qid="Q21",
            ),
        },
    )
    assert outcomes[0].status == "would_adopt"
    assert "explicit accept_foreign_modify" in outcomes[0].message


def test_foreign_accept_ignored_on_test_upload(monkeypatch):
    item = _manuscript("990000021")
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
    )
    monkeypatch.setattr(
        wu, "_make_reconciler",
        lambda: _FakeReconciler(ms_map={"990000021": "Q21"}),
    )

    class _ForeignUploader:
        def __init__(self, token, is_test, batch_mode, **_kwargs):
            assert is_test is True
            self._is_our_item_cache = {}
            self.registered: list[str] = []

        def register_foreign_accept(self, qid: str) -> None:
            self.registered.append(qid)

        def _is_our_item(self, qid: str) -> bool:
            return False

        def upload_item(self, item, **kwargs):
            assert item.existing_qid is None
            return SimpleNamespace(
                local_id=item.local_id,
                qid="Q999",
                status="success",
                message="created on test",
                added_properties=[],
            )

    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", _ForeignUploader)

    outcomes = wu._upload_sync(
        [item],
        token="User@Bot:deadbeef",
        dry_run=False,
        ledger={},
        ledger_ns="wikidata",
        is_test=True,
        accept_by_local_id={
            "990000021": wu.ForeignAccept(
                accept_foreign_modify=True, accepted_foreign_qid="Q21",
            ),
        },
    )
    assert outcomes[0].status == "created"
    assert outcomes[0].qid == "Q999"


def test_reconcile_preview_marks_outage_as_error(monkeypatch):
    monkeypatch.setattr(wu, "_make_reconciler", lambda: _FakeReconciler(raise_on="manuscript"))

    outcomes = wu._reconcile_sync([_manuscript("990001234")])

    assert outcomes[0].method == "error"
    assert outcomes[0].existing_qid is None


# ── Reconciler unit guards (the new fail-closed + P3959 behaviour) ────────


def test_query_raises_reconciliation_unavailable_on_network_error(monkeypatch):
    import requests

    rec = WikidataReconciler()

    def _boom(*args, **kwargs):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(rec._session, "get", _boom)

    with pytest.raises(ReconciliationUnavailableError):
        rec.reconcile_manuscript_by_nnl_id("990001234")


def test_manuscript_by_identifiers_tries_p3959_then_shelfmark(monkeypatch):
    rec = WikidataReconciler()
    monkeypatch.setattr(rec, "reconcile_manuscript_by_nnl_id", lambda _id: None)
    monkeypatch.setattr(rec, "reconcile_manuscript_by_shelfmark", lambda _s: "Q77")

    assert rec.reconcile_manuscript_by_identifiers("990x", "MS 5") == "Q77"


def test_manuscript_by_identifiers_short_circuits_on_p3959(monkeypatch):
    rec = WikidataReconciler()
    monkeypatch.setattr(rec, "reconcile_manuscript_by_nnl_id", lambda _id: "Q1")

    def _should_not_run(_s):
        raise AssertionError("shelfmark fallback must not run when P3959 matched")

    monkeypatch.setattr(rec, "reconcile_manuscript_by_shelfmark", _should_not_run)
    assert rec.reconcile_manuscript_by_identifiers("990x", "MS 5") == "Q1"


def test_person_by_identifiers_rejects_on_cross_identifier_conflict(monkeypatch):
    rec = WikidataReconciler()
    monkeypatch.setattr(rec, "reconcile_person_by_viaf", lambda _v: "Q1")
    # Candidate Q1 conflicts on P8189 → must be rejected (treated as a
    # different real-world entity), returning None.
    monkeypatch.setattr(rec, "_candidate_conflicts", lambda _q, _p: ["P8189"])

    assert rec.reconcile_person_by_identifiers("111", "9870555") is None


def test_person_by_identifiers_accepts_when_no_conflict(monkeypatch):
    rec = WikidataReconciler()
    monkeypatch.setattr(rec, "reconcile_person_by_viaf", lambda _v: "Q1")
    monkeypatch.setattr(rec, "_candidate_conflicts", lambda _q, _p: [])

    assert rec.reconcile_person_by_identifiers("111", "9870555") == "Q1"


@pytest.mark.asyncio
async def test_live_upload_records_audit_rows(db_session, sample_run, monkeypatch) -> None:
    from sqlalchemy import select

    from app.models.wikibase_cloud_write import (
        CHANNEL_WIKIDATA_UPLOAD,
        OPERATION_ADOPT,
        OPERATION_SKIP,
        WikibaseCloudWrite,
    )
    from app.services.wikibase_audit import WikibaseAuditContext

    monkeypatch.setenv("WIKIDATA_TEST_MODE", "true")
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
    )

    good = _manuscript("990000001")
    block_item = _Item(entity_type="person", labels={"en": "Winter"}, statements=[], local_id="winter")

    class _Rec(_FakeReconciler):
        def reconcile_manuscript_by_identifiers(self, nnl_id, shelfmark):
            self.ms_calls.append((nnl_id, shelfmark))
            return {"990000001": "Q1"}.get(nnl_id)

    monkeypatch.setattr(wu, "_make_reconciler", lambda: _Rec())

    class _FakeUploader:
        def __init__(self, token, is_test, batch_mode, **_kwargs):
            self._is_our_item_cache = {}

        def _is_our_item(self, qid: str) -> bool:
            return True

        def upload_item(self, item, **kwargs):
            return _FakeResult(qid="Q1", status="updated", message="Updated Q1")

    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", _FakeUploader)

    outcomes = await wu.upload_items(
        [good, block_item],
        token="User@Bot:deadbeef",
        dry_run=False,
        audit_ctx=WikibaseAuditContext(
            actor_user_id=sample_run["user_id"],
            channel=CHANNEL_WIKIDATA_UPLOAD,
            run_id=sample_run["run_id"],
        ),
        db=db_session,
    )
    assert len(outcomes) == 2
    rows = (
        await db_session.execute(
            select(WikibaseCloudWrite).where(
                WikibaseCloudWrite.run_id == sample_run["run_id"],
                WikibaseCloudWrite.channel == CHANNEL_WIKIDATA_UPLOAD,
            )
        )
    ).scalars().all()
    ops = {r.target_key: r.operation for r in rows}
    assert ops["990000001"] == OPERATION_ADOPT
    assert ops["winter"] == OPERATION_SKIP


@pytest.mark.asyncio
async def test_dry_run_writes_no_audit_rows(db_session, sample_run, monkeypatch) -> None:
    from sqlalchemy import select

    from app.models.wikibase_cloud_write import CHANNEL_WIKIDATA_UPLOAD, WikibaseCloudWrite
    from app.services.wikibase_audit import WikibaseAuditContext

    monkeypatch.setattr(wu, "_make_reconciler", lambda: _FakeReconciler())

    await wu.upload_items(
        [_manuscript("990000001")],
        token="",
        dry_run=True,
        audit_ctx=WikibaseAuditContext(
            actor_user_id=sample_run["user_id"],
            channel=CHANNEL_WIKIDATA_UPLOAD,
            run_id=sample_run["run_id"],
        ),
        db=db_session,
    )
    count = (
        await db_session.execute(
            select(WikibaseCloudWrite).where(
                WikibaseCloudWrite.run_id == sample_run["run_id"],
                WikibaseCloudWrite.channel == CHANNEL_WIKIDATA_UPLOAD,
            )
        )
    ).scalars().all()
    assert count == []


def test_resolve_upload_mode_defaults_to_dry_run():
    mode = wu.resolve_upload_mode(None)
    assert mode.target == "dry_run"
    assert mode.dry_run is True
    assert mode.is_test is False
    assert mode.allow_live is False


def test_resolve_upload_mode_test_and_live():
    test = wu.resolve_upload_mode("test")
    assert test.target == "test"
    assert test.dry_run is False
    assert test.is_test is True
    assert test.allow_live is False
    assert test.moratorium_lifted is True

    live = wu.resolve_upload_mode("live")
    assert live.target == "live"
    assert live.dry_run is False
    assert live.is_test is False
    assert live.allow_live is True
    assert live.moratorium_lifted is True


def test_ui_live_target_bypasses_env_moratorium(monkeypatch):
    monkeypatch.delenv("MORATORIUM_LIFTED", raising=False)
    monkeypatch.delenv("WIKIDATA_TEST_MODE", raising=False)
    monkeypatch.setattr(wu, "_make_reconciler", lambda: _FakeReconciler(
        ms_map={"990000001": None},
    ))
    written: list = []

    class _FakeUploader:
        def __init__(self, token, is_test, batch_mode, **kwargs):
            assert is_test is False
            assert kwargs.get("allow_live") is True
            self._is_our_item_cache = {}

        def upload_item(self, item, **kwargs):
            written.append(item)
            return _FakeResult(qid="Q77", status="success", message="Created Q77")

    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", _FakeUploader)
    outcomes = wu._upload_sync(
        [_manuscript("990000001")],
        token="User@Bot:deadbeef",
        dry_run=False,
        ledger={},
        ledger_ns="wikidata",
        is_test=False,
        allow_live=True,
    )
    assert len(written) == 1


def test_upload_sync_aborts_on_permissiondenied(monkeypatch):
    monkeypatch.setenv("WIKIDATA_TEST_MODE", "true")
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
    )
    monkeypatch.setattr(wu, "_make_reconciler", lambda: _FakeReconciler())

    calls = {"n": 0}

    class _FakeUploader:
        def __init__(self, token, is_test, batch_mode, **_kwargs):
            self._is_our_item_cache = {}

        def _is_our_item(self, qid: str) -> bool:
            return True

        def upload_item(self, item, **kwargs):
            calls["n"] += 1
            return _FakeResult(
                qid=None,
                status="failed",
                message="MediaWiki permissiondenied on test.wikidata.org",
            )

    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", _FakeUploader)

    items = [_manuscript(f"9900000{i:02d}") for i in range(3)]
    shared = _FakeUploader("x", True, True)
    outcomes = wu._upload_sync(
        items,
        token="User@Bot:deadbeef",
        dry_run=False,
        ledger={},
        ledger_ns="wikidata",
        uploader=shared,
    )
    assert calls["n"] == 1
    assert outcomes[0].status == "failed"
    assert len(outcomes) == 1


# ── P3959 is identity evidence, not ownership ─────────────────────────────


def _p3959_uploader(monkeypatch, *, is_test: bool, remote_claims: dict, creator: str):
    from converter.wikidata.uploader import WikidataUploader

    up = WikidataUploader(token="User@Bot:deadbeef", is_test=is_test)
    monkeypatch.setattr(up, "_get_authenticated_user", lambda: "MHMBot")
    monkeypatch.setattr(up, "_get_first_revision_author", lambda _qid: creator)
    monkeypatch.setattr(up, "_user_created_via_contribs", lambda *_a: True)
    monkeypatch.setattr(up, "_item_exists_on_wikidata_sparql", lambda _qid: True)
    monkeypatch.setattr(
        up,
        "_wbgetentities",
        lambda ids, *, props: {
            i: {"id": i, "lastrevid": 7, "claims": remote_claims} for i in ids
        },
    )
    return up


def _remote_p3959_claim(value: str) -> dict:
    return {
        "P3959": [
            {
                "mainsnak": {
                    "snaktype": "value",
                    "property": "P3959",
                    "datavalue": {"value": value, "type": "string"},
                }
            }
        ]
    }


def test_p3959_identity_does_not_authorize_a_foreign_created_manuscript(monkeypatch):
    from converter.wikidata.uploader import UnauthorisedModificationError

    up = _p3959_uploader(
        monkeypatch,
        is_test=False,
        remote_claims=_remote_p3959_claim("990000533850205171"),
        creator="Lapon-Kandelshein Essi",
    )
    item = _manuscript("990000533850205171", qid="Q134619072")

    assert up._our_p3959(item) == "990000533850205171"
    assert up._p3959_identity_accepts("Q134619072", up._our_p3959(item)) is True
    assert up._is_our_item("Q134619072", our_p3959=up._our_p3959(item)) is False
    with pytest.raises(UnauthorisedModificationError):
        up._assert_modifiable(
            "Q134619072", stage="pre_write", our_p3959=up._our_p3959(item),
        )


def test_p3959_absent_on_remote_refuses_modification(monkeypatch):
    from converter.wikidata.uploader import UnauthorisedModificationError

    up = _p3959_uploader(
        monkeypatch,
        is_test=False,
        remote_claims={},
        creator="A Community Member",
    )
    item = _manuscript("990000533850205171", qid="Q134619072")

    assert up._is_our_item("Q134619072", our_p3959=up._our_p3959(item)) is False
    with pytest.raises(UnauthorisedModificationError):
        up._assert_modifiable(
            "Q134619072",
            stage="pre_write",
            our_p3959=up._our_p3959(item),
        )


def test_p3959_different_on_remote_refuses_modification(monkeypatch):
    from converter.wikidata.uploader import UnauthorisedModificationError

    up = _p3959_uploader(
        monkeypatch,
        is_test=False,
        remote_claims=_remote_p3959_claim("990000533850205172"),
        creator="A Community Member",
    )
    item = _manuscript("990000533850205171", qid="Q134619072")

    assert up._is_our_item("Q134619072", our_p3959=up._our_p3959(item)) is False
    with pytest.raises(UnauthorisedModificationError):
        up._assert_modifiable(
            "Q134619072",
            stage="pre_write",
            our_p3959=up._our_p3959(item),
        )


def test_p3959_identity_never_applies_on_the_test_wiki(monkeypatch):
    up = _p3959_uploader(
        monkeypatch,
        is_test=True,
        remote_claims=_remote_p3959_claim("990000533850205171"),
        creator="A Community Member",
    )
    item = _manuscript("990000533850205171", qid="Q134619072")

    assert up._p3959_identity_accepts("Q134619072", up._our_p3959(item)) is False
    assert up._is_our_item("Q134619072", our_p3959=up._our_p3959(item)) is False


def test_p3959_channel_does_not_apply_to_persons_and_works(monkeypatch):
    up = _p3959_uploader(
        monkeypatch,
        is_test=False,
        remote_claims=_remote_p3959_claim("990000533850205171"),
        creator="A Community Member",
    )
    person = _person(qid="Q16130567")
    person.statements.append(_Stmt("P3959", "990000533850205171"))
    work = _Item(
        entity_type="work",
        labels={"he": "משנה"},
        statements=[_Stmt("P3959", "990000533850205171")],
        existing_qid="Q191825",
        local_id="work:1",
    )

    assert up._our_p3959(person) is None
    assert up._our_p3959(work) is None
    assert up._is_our_item("Q16130567", our_p3959=up._our_p3959(person)) is False
    assert up._is_our_item("Q191825", our_p3959=up._our_p3959(work)) is False
