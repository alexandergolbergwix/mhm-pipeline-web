"""Unit tests for HMO schema AI verify entry filtering."""

from __future__ import annotations

from app.pipeline.hmo_schema_bootstrap import SchemaBootstrapEntry, SchemaBootstrapResult
from app.pipeline.hmo_schema_verify import filter_schema_entries


def _report(*statuses: str) -> SchemaBootstrapResult:
    entries = [
        SchemaBootstrapEntry(
            ontology_uri=f"http://example.org#{status}",
            entity_kind="property",
            label=status,
            wikibase_id="P1" if status == "skipped" else None,
            status=status,
            message="",
        )
        for status in statuses
    ]
    return SchemaBootstrapResult(
        dry_run=True,
        created=0,
        skipped=sum(1 for s in statuses if s == "skipped"),
        failed=sum(1 for s in statuses if s == "failed"),
        would_create=sum(1 for s in statuses if s == "would_create"),
        entries=entries,
    )


def test_filter_schema_entries_includes_skipped_rows() -> None:
    report = _report("skipped", "would_create", "failed", "created")

    items = filter_schema_entries(report, ontology_uris=None)

    assert {item["status"] for item in items} == {
        "skipped",
        "would_create",
        "failed",
        "created",
    }


def test_filter_schema_entries_honours_ontology_uri_selection() -> None:
    report = _report("skipped", "would_create")

    items = filter_schema_entries(
        report,
        ontology_uris=["http://example.org#skipped"],
    )

    assert len(items) == 1
    assert items[0]["ontology_uri"] == "http://example.org#skipped"
