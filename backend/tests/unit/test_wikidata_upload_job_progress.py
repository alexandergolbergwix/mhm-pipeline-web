"""Live upload progress payload helpers (Rule W-141 modal)."""

from __future__ import annotations

from types import SimpleNamespace

from app.pipeline.wikidata_upload_job import (
    slim_upload_progress_outcome,
    upload_outcome_counts,
)


def test_slim_upload_progress_outcome_includes_label_and_entity_type() -> None:
    row = slim_upload_progress_outcome(
        SimpleNamespace(
            local_id="QDraft_MS_1",
            label="Ms. Heb. 1",
            entity_type="manuscript",
            status="success",
            qid="Q999",
            message="Created Q999 (3 claims)",
        ),
    )
    assert row["local_id"] == "QDraft_MS_1"
    assert row["label"] == "Ms. Heb. 1"
    assert row["entity_type"] == "manuscript"
    assert row["status"] == "success"
    assert row["qid"] == "Q999"
    assert row["wikibase_id"] == "Q999"


def test_upload_outcome_counts_tallies_statuses() -> None:
    outcomes = [
        SimpleNamespace(status="success"),
        SimpleNamespace(status="updated"),
        SimpleNamespace(status="adopted"),
        SimpleNamespace(status="blocked"),
        SimpleNamespace(status="skipped"),
        SimpleNamespace(status="failed"),
        SimpleNamespace(status="pending"),
    ]
    counts = upload_outcome_counts(outcomes)
    assert counts == {
        "created": 1,
        "updated": 1,
        "adopted": 1,
        "blocked": 1,
        "skipped": 1,
        "failed": 1,
        "pending": 1,
    }


def test_slim_upload_progress_outcome_processing_shape() -> None:
    row = slim_upload_progress_outcome(
        SimpleNamespace(
            local_id="QDraft_MS_1",
            label="Ms. Heb. 1",
            entity_type="manuscript",
            status="processing",
            qid=None,
            message="Processing…",
        ),
    )
    assert row["status"] == "processing"
    assert row["message"] == "Processing…"
    assert row["qid"] is None
