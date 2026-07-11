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
from typing import Any

import pytest

from app.pipeline import wikidata_upload as wu
from converter.wikidata.reconciler import (
    ReconciliationUnavailableError,
    WikidataReconciler,
)

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


def test_validator_blocks_person_with_no_identifier():
    # "Winter": single short Latin name, no external id → NO_IDENTIFIER +
    # AMBIGUOUS_SINGLE_NAME errors. Reconciliation returns None (no ids), so
    # only the validator gate can stop it — and it must.
    item = _Item(entity_type="person", labels={"en": "Winter"},
                 statements=[], existing_qid="", local_id="winter")
    rec = _FakeReconciler()

    prepared = wu._prepare_for_upload([item], rec)

    assert prepared[0].blocked is True
    assert "NO_IDENTIFIER" in prepared[0].block_message


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


# ── _upload_sync: blocked items never reach the uploader ──────────────────


def test_upload_sync_never_writes_blocked_items(monkeypatch):
    monkeypatch.setenv("WIKIDATA_TEST_MODE", "true")
    monkeypatch.delenv("MORATORIUM_LIFTED", raising=False)

    good = _manuscript("990000001")            # matches → update
    outage = _manuscript("990000002")          # lookup fails → blocked

    # Route through a reconciler that raises for a specific id so only the
    # second item's lookup fails.
    class _SelectiveRec(_FakeReconciler):
        def reconcile_manuscript_by_identifiers(self, nnl_id, shelfmark):
            self.ms_calls.append((nnl_id, shelfmark))
            if nnl_id == "990000002":
                raise ReconciliationUnavailableError("timeout")
            return {"990000001": "Q1"}.get(nnl_id)

    monkeypatch.setattr(wu, "_make_reconciler", lambda: _SelectiveRec())

    written: list = []

    class _FakeUploader:
        def __init__(self, token, is_test, batch_mode):
            assert is_test is True

        def upload_item(self, item):
            written.append(item)
            return _FakeResult(qid="Q1", status="updated", message="Updated Q1: +3 claims")

    monkeypatch.setattr("converter.wikidata.uploader.WikidataUploader", _FakeUploader)

    outcomes = wu._upload_sync(
        [good, outage], token="User@Bot:deadbeef", dry_run=False, ledger={}, ledger_ns="wikidata",
    )

    statuses = {o.local_id: o.status for o in outcomes}
    assert statuses["990000001"] == "adopted"
    assert statuses["990000002"] == "blocked"
    # The blocked item must never have been written.
    assert written == [good]


def test_dry_run_reports_update_create_and_block(monkeypatch):
    update_item = _manuscript("990000010")
    create_item = _manuscript("990000011")
    block_item = _manuscript("990000012")

    class _Rec(_FakeReconciler):
        def reconcile_manuscript_by_identifiers(self, nnl_id, shelfmark):
            if nnl_id == "990000012":
                raise ReconciliationUnavailableError("503")
            return {"990000010": "Q10"}.get(nnl_id)

    monkeypatch.setattr(wu, "_make_reconciler", lambda: _Rec())

    outcomes = wu._upload_sync(
        [update_item, create_item, block_item], token="", dry_run=True,
        ledger={}, ledger_ns="wikidata",
    )
    by_id = {o.local_id: o for o in outcomes}

    assert by_id["990000010"].status == "would_adopt"
    assert by_id["990000010"].qid == "Q10"
    assert by_id["990000011"].status == "success"
    assert by_id["990000012"].status == "blocked"


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
        OPERATION_BLOCKED,
        WikibaseCloudWrite,
    )
    from app.services.wikibase_audit import WikibaseAuditContext

    monkeypatch.setenv("WIKIDATA_TEST_MODE", "true")

    good = _manuscript("990000001")
    block_item = _Item(entity_type="person", labels={"en": "Winter"}, statements=[], local_id="winter")

    class _Rec(_FakeReconciler):
        def reconcile_manuscript_by_identifiers(self, nnl_id, shelfmark):
            self.ms_calls.append((nnl_id, shelfmark))
            return {"990000001": "Q1"}.get(nnl_id)

    monkeypatch.setattr(wu, "_make_reconciler", lambda: _Rec())

    class _FakeUploader:
        def __init__(self, token, is_test, batch_mode):
            pass

        def upload_item(self, item):
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
    assert ops["winter"] == OPERATION_BLOCKED


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
