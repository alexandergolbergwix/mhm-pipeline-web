"""Tests for the live HMO Wikibase schema bootstrap pipeline (Phase 3 —
see dev-docs/hmo-wikibase-studio-plan.md).

Pins the idempotency contract: a first bootstrap creates every missing
ontology class/property and records a mapping row per success; a
second run against the same (unchanged) mapping table creates nothing.
A mid-batch failure must not roll back the mappings already committed.

The real ontology (~380 entries) isn't needed to prove this — every
test monkeypatches ``read_hmo_schema`` with a tiny synthetic schema so
the suite stays fast and focused on the bootstrap logic itself.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import select

from app.models.wikibase_entity_mapping import (
    ENTITY_KIND_CLASS,
    ENTITY_KIND_PROPERTY,
    WikibaseEntityMapping,
)
from app.pipeline import hmo_schema_bootstrap as pipeline
from converter.wikibase.ontology_schema_reader import (
    OntologyClassEntry,
    OntologyPropertyEntry,
    OntologySchema,
)


def _tiny_schema() -> OntologySchema:
    return OntologySchema(
        classes=[
            OntologyClassEntry(
                uri="http://example.org#Manuscript",
                local_name="Manuscript",
                label="Manuscript",
                description="A physical manuscript.",
                aliases=["כתב יד"],
            ),
        ],
        properties=[
            OntologyPropertyEntry(
                uri="http://example.org#has_folio_count",
                local_name="has_folio_count",
                label="has folio count",
                description="Number of folios.",
                datatype="string",
            ),
        ],
    )


@dataclass
class _FakeOutcome:
    entity_id: str | None
    status: str = "created"
    message: str = "ok"
    page_url: str | None = None


class _FakeWriter:
    """Records calls and hands out sequential QIDs/PIDs."""

    def __init__(self) -> None:
        self.property_calls: list[dict] = []
        self.item_calls: list[dict] = []
        self._next_p = 1
        self._next_q = 1

    def create_property(self, **kwargs):
        self.property_calls.append(kwargs)
        pid = f"P{self._next_p}"
        self._next_p += 1
        return _FakeOutcome(entity_id=pid)

    def create_item(self, **kwargs):
        self.item_calls.append(kwargs)
        qid = f"Q{self._next_q}"
        self._next_q += 1
        return _FakeOutcome(entity_id=qid)


class _FailingWriter(_FakeWriter):
    """Fails every property create, succeeds on item create."""

    def create_property(self, **kwargs):
        self.property_calls.append(kwargs)
        return _FakeOutcome(entity_id=None, status="failed", message="boom")


@pytest.fixture
def tiny_schema(monkeypatch: pytest.MonkeyPatch) -> OntologySchema:
    schema = _tiny_schema()
    monkeypatch.setattr(
        "converter.wikibase.ontology_schema_reader.read_hmo_schema", lambda: schema
    )
    return schema


@pytest.mark.asyncio
async def test_dry_run_reports_would_create_without_writing(
    db_session, tiny_schema
) -> None:
    result = await pipeline.bootstrap_schema(db_session, writer=None, dry_run=True)

    assert result.dry_run is True
    assert result.created == 0
    assert result.would_create == 2
    assert {e.status for e in result.entries} == {"would_create"}
    assert len(result.entries) == 2

    rows = (await db_session.execute(select(WikibaseEntityMapping))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_live_bootstrap_creates_and_records_mappings(
    db_session, tiny_schema
) -> None:
    writer = _FakeWriter()

    result = await pipeline.bootstrap_schema(db_session, writer=writer, dry_run=False)

    assert result.created == 2
    assert result.skipped == 0
    assert result.failed == 0
    assert len(writer.property_calls) == 1
    assert len(writer.item_calls) == 1

    rows = (await db_session.execute(select(WikibaseEntityMapping))).scalars().all()
    assert len(rows) == 2
    kinds = {row.entity_kind for row in rows}
    assert kinds == {ENTITY_KIND_CLASS, ENTITY_KIND_PROPERTY}
    assert all(row.run_id is None for row in rows)


@pytest.mark.asyncio
async def test_second_bootstrap_run_creates_nothing(db_session, tiny_schema) -> None:
    first_writer = _FakeWriter()
    await pipeline.bootstrap_schema(db_session, writer=first_writer, dry_run=False)

    second_writer = _FakeWriter()
    result = await pipeline.bootstrap_schema(db_session, writer=second_writer, dry_run=False)

    assert result.created == 0
    assert result.skipped == 2
    assert second_writer.property_calls == []
    assert second_writer.item_calls == []

    rows = (await db_session.execute(select(WikibaseEntityMapping))).scalars().all()
    assert len(rows) == 2  # no duplicates


@pytest.mark.asyncio
async def test_partial_failure_keeps_prior_successful_mappings(
    db_session, tiny_schema
) -> None:
    writer = _FailingWriter()

    result = await pipeline.bootstrap_schema(db_session, writer=writer, dry_run=False)

    assert result.failed == 1  # the property create failed
    assert result.created == 1  # the class create still succeeded
    statuses = {e.ontology_uri: e.status for e in result.entries}
    assert statuses["http://example.org#has_folio_count"] == "failed"
    assert statuses["http://example.org#Manuscript"] == "created"

    rows = (await db_session.execute(select(WikibaseEntityMapping))).scalars().all()
    assert len(rows) == 1
    assert rows[0].entity_kind == ENTITY_KIND_CLASS


@pytest.mark.asyncio
async def test_schema_status_reports_counts_and_missing_sample(
    db_session, tiny_schema
) -> None:
    before = await pipeline.schema_status(db_session)
    assert before.total_classes == 1
    assert before.total_properties == 1
    assert before.mapped_classes == 0
    assert before.mapped_properties == 0
    assert set(before.missing_sample) == {
        "http://example.org#Manuscript",
        "http://example.org#has_folio_count",
    }

    await pipeline.bootstrap_schema(db_session, writer=_FakeWriter(), dry_run=False)

    after = await pipeline.schema_status(db_session)
    assert after.mapped_classes == 1
    assert after.mapped_properties == 1
    assert after.missing_sample == []


def test_build_wikibase_labels_disambiguates_duplicate_en_labels() -> None:
    ordered = [
        (
            "http://cidoc/P46",
            ENTITY_KIND_PROPERTY,
            "P46_is_composed_of",
            "is composed of",
            "cidoc",
            [],
            "wikibase-item",
        ),
        (
            "http://hmo/is_composed_of",
            ENTITY_KIND_PROPERTY,
            "is_composed_of",
            "is composed of",
            "hmo",
            [],
            "wikibase-item",
        ),
    ]
    labels = pipeline.build_wikibase_labels(ordered)
    assert labels["http://cidoc/P46"] == "is composed of"
    assert labels["http://hmo/is_composed_of"] == "is composed of (is_composed_of)"


@pytest.mark.asyncio
async def test_stale_bootstrap_report_is_regenerated_when_fully_mapped(
    db_session, tiny_schema, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await pipeline.bootstrap_schema(db_session, writer=_FakeWriter(), dry_run=False)

    stale = pipeline.SchemaBootstrapResult(
        dry_run=True,
        created=0,
        skipped=0,
        failed=0,
        would_create=2,
        entries=[
            pipeline.SchemaBootstrapEntry(
                ontology_uri="http://example.org#Manuscript",
                entity_kind=ENTITY_KIND_CLASS,
                label="Manuscript",
                wikibase_id=None,
                status="would_create",
                message="",
            ),
            pipeline.SchemaBootstrapEntry(
                ontology_uri="http://example.org#has_folio_count",
                entity_kind=ENTITY_KIND_PROPERTY,
                label="has folio count",
                wikibase_id=None,
                status="would_create",
                message="",
            ),
        ],
    )
    monkeypatch.setattr(pipeline, "_SCHEMA_STATE_ROOT", tmp_path)
    pipeline.cache_schema_bootstrap_report(stale)

    report = await pipeline.load_last_bootstrap_report(db_session)

    assert report is not None
    assert report.would_create == 0
    assert report.skipped == 2
    assert {e.status for e in report.entries} == {"skipped"}


@pytest.mark.asyncio
async def test_live_bootstrap_uses_disambiguated_labels_for_duplicates(
    db_session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = OntologySchema(
        classes=[],
        properties=[
            OntologyPropertyEntry(
                uri="http://cidoc/P46",
                local_name="P46_is_composed_of",
                label="is composed of",
                description="cidoc",
                datatype="wikibase-item",
            ),
            OntologyPropertyEntry(
                uri="http://hmo/is_composed_of",
                local_name="is_composed_of",
                label="is composed of",
                description="hmo",
                datatype="wikibase-item",
            ),
        ],
    )
    monkeypatch.setattr(
        "converter.wikibase.ontology_schema_reader.read_hmo_schema", lambda: schema
    )
    writer = _FakeWriter()
    result = await pipeline.bootstrap_schema(db_session, writer=writer, dry_run=False)

    assert result.created == 2
    assert writer.property_calls[0]["labels"]["en"] == "is composed of"
    assert writer.property_calls[1]["labels"]["en"] == "is composed of (is_composed_of)"
