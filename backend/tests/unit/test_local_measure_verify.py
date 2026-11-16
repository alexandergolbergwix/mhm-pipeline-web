"""Measurement must preserve the same curator state as a production build."""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy import event

from app.models.hmo_canonical_entity import HmoCanonicalEntity
from app.models.item_override import WikidataItemOverride
from app.pipeline.wikidata_item_merge import override_row_to_dict


@pytest.mark.asyncio
async def test_canonical_measurement_retains_complete_curator_state_without_database_writes(
    db_session, sample_run, _engine, monkeypatch, tmp_path,
):
    from app.pipeline import hmo_canonical_wikidata, wikidata_studio
    from scripts.local_measure_verify import WikidataChannel

    run_id = sample_run["run_id"]
    override = WikidataItemOverride(
        run_id=run_id, local_id="person:reviewed",
        labels={"en": "Reviewed name"}, approved=False,
        accepted_foreign_qid="Q123", accept_foreign_modify=False,
        person_identity_choice={"action": "reference", "target_qid": "Q123"},
        source_review_choices=[{"candidate_key": "source:work", "target_qid": "Q456"}],
    )
    db_session.add(override)
    db_session.add(HmoCanonicalEntity(
        run_id=run_id, local_id="person:reviewed", source_uri="https://example.org/person",
        wikibase_id="Q1", source_fingerprint="0" * 64,
        snapshot={"local_id": "person:reviewed", "source_uri": "https://example.org/person",
                  "wikibase_id": "Q1"},
    ))
    await db_session.commit()
    expected = deepcopy(override_row_to_dict(override))
    monkeypatch.setattr(
        wikidata_studio, "build_items_for_run",
        AsyncMock(return_value={"items": [], "native_items": []}),
    )
    observed = {}

    def canonical(snapshots, **kwargs):
        observed.update(deepcopy(kwargs["overrides"]))
        return {"items": [], "native_items": []}

    monkeypatch.setattr(hmo_canonical_wikidata, "build_canonical_studio_result", canonical)
    mutations = []

    def watch(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            mutations.append(statement)

    event.listen(_engine.sync_engine, "before_cursor_execute", watch)
    try:
        channel = WikidataChannel()
        channel.audit_projection = "canonical"
        await channel.build_measurement(run_id, tmp_path)
    finally:
        event.remove(_engine.sync_engine, "before_cursor_execute", watch)
    assert observed["person:reviewed"] == expected
    assert observed["person:reviewed"]["approved"] is False
    assert observed["person:reviewed"]["accepted_foreign_qid"] == "Q123"
    assert not mutations


@pytest.mark.asyncio
async def test_canonical_measurement_reads_complete_snapshots_in_bounded_pages(
    db_session, sample_run, _engine, monkeypatch, tmp_path,
):
    from app.pipeline import hmo_canonical_wikidata, wikidata_studio
    from scripts.local_measure_verify import WikidataChannel

    run_id = sample_run["run_id"]
    expected = []
    for index in range(205):
        local_id = f"work:page:{index:03d}"
        labels = {"en": f"Work {index}"}
        evidence = [{"source": "fixture", "nested": {"index": index}}]
        db_session.add(HmoCanonicalEntity(
            id=UUID(int=index + 1), run_id=run_id, local_id=local_id,
            source_uri=f"https://example.org/work/{index}", wikibase_id=f"Q{index + 1}",
            source_fingerprint="0" * 64,
            snapshot={"local_id": local_id, "source_uri": f"https://example.org/work/{index}",
                      "wikibase_id": f"Q{index + 1}", "labels": labels,
                      "authority_evidence": evidence},
        ))
        expected.append((local_id, labels, evidence))
    db_session.add(HmoCanonicalEntity(
        id=UUID(int=1000), run_id=UUID(int=1001), local_id="other-run",
        source_uri="https://example.org/other", wikibase_id="Q999",
        source_fingerprint="0" * 64,
        snapshot={"local_id": "other-run", "source_uri": "https://example.org/other",
                  "wikibase_id": "Q999"},
    ))
    await db_session.commit()
    monkeypatch.setattr(
        wikidata_studio, "build_items_for_run",
        AsyncMock(return_value={"items": [], "native_items": []}),
    )
    observed = []

    def canonical(snapshots, **kwargs):
        observed.extend((row.local_id, row.labels, row.authority_evidence) for row in snapshots)
        return {"items": [], "native_items": []}

    monkeypatch.setattr(hmo_canonical_wikidata, "build_canonical_studio_result", canonical)
    reads = []
    mutations = []

    def watch(conn, cursor, statement, parameters, context, many):
        upper = statement.lstrip().upper()
        if upper.startswith("SELECT") and "FROM hmo_canonical_entities" in statement:
            reads.append((statement, parameters))
        if upper.startswith(("INSERT", "UPDATE", "DELETE")):
            mutations.append(statement)

    event.listen(_engine.sync_engine, "before_cursor_execute", watch)
    try:
        channel = WikidataChannel()
        channel.audit_projection = "canonical"
        await channel.build_measurement(run_id, tmp_path)
    finally:
        event.remove(_engine.sync_engine, "before_cursor_execute", watch)
    assert observed == expected
    assert reads
    assert all("LIMIT" in statement for statement, _ in reads), "A snapshot read is unbounded"
    assert all(0 < parameters[-2] <= 50 for _, parameters in reads)
    assert len(reads) == 5
    assert not mutations


@pytest.mark.asyncio
async def test_canonical_measurement_sets_postgres_transaction_before_its_first_read(
    monkeypatch, tmp_path,
):
    from scripts import local_measure_verify

    class EmptyResult:
        def scalars(self):
            return self

        def all(self):
            return []

    db = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
        execute=AsyncMock(return_value=EmptyResult()),
    )

    @asynccontextmanager
    async def scope():
        yield db

    monkeypatch.setattr(local_measure_verify, "session_scope", scope)
    channel = local_measure_verify.WikidataChannel()
    channel.audit_projection = "canonical"
    with pytest.raises(SystemExit, match="no MARC records"):
        await channel.build_measurement(UUID(int=2000), tmp_path)
    statements = [str(call.args[0]) for call in db.execute.await_args_list]
    assert statements[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    assert statements[1].startswith("SELECT")


@pytest.mark.asyncio
async def test_hmo_measurement_does_not_require_a_wikidata_projection(
    db_session, tmp_path,
):
    from scripts.local_measure_verify import HmoChannel

    with pytest.raises(SystemExit, match="no MARC records"):
        await HmoChannel().build_measurement(UUID(int=2001), tmp_path)
