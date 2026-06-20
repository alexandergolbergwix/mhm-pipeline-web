"""Auto-approve preview must use the same entity source as GET /entities."""

from __future__ import annotations

import uuid

import pytest

from app.models.extraction_approval import ExtractionApproval
from app.routers.extraction import AutoApprovePayload, _auto_approve_eligible


@pytest.mark.asyncio
async def test_auto_approve_preview_uses_db_rows_without_ner_file(
    db_session, sample_run, monkeypatch,
) -> None:
    run_id = sample_run["run_id"]
    row = ExtractionApproval(
        run_id=run_id,
        control_number=sample_run["control_number"],
        source="person_ner",
        text="Moses Maimonides",
        start=10,
        end=28,
        type="PERSON",
        role="author",
        confidence=0.85,
        model_confidence=0.92,
        approved=False,
    )
    db_session.add(row)
    await db_session.commit()

    def _no_ner_file(_run_id: uuid.UUID):
        from pathlib import Path
        return Path("/tmp/does-not-exist/ner_results.json")

    monkeypatch.setattr("app.routers.extraction._results_path", _no_ner_file)

    payload = AutoApprovePayload(min_confidence=0.6, require_ai_pass=False)
    eligible = await _auto_approve_eligible(db_session, run_id, payload)
    assert len(eligible) == 1
    assert eligible[0]["text"] == "Moses Maimonides"


@pytest.mark.asyncio
async def test_auto_approve_require_ai_pass_filters_unjudged(
    db_session, sample_run, monkeypatch,
) -> None:
    run_id = sample_run["run_id"]
    db_session.add(
        ExtractionApproval(
            run_id=run_id,
            control_number=sample_run["control_number"],
            source="person_ner",
            text="Judged pass",
            start=0,
            end=5,
            type="PERSON",
            role="author",
            model_confidence=0.9,
            approved=False,
            ai_verdict={"overall": "pass"},
        )
    )
    db_session.add(
        ExtractionApproval(
            run_id=run_id,
            control_number=sample_run["control_number"],
            source="person_ner",
            text="Not judged",
            start=6,
            end=12,
            type="PERSON",
            role="scribe",
            model_confidence=0.9,
            approved=False,
        )
    )
    await db_session.commit()

    def _no_ner_file(_run_id: uuid.UUID):
        from pathlib import Path
        return Path("/tmp/does-not-exist/ner_results.json")

    monkeypatch.setattr("app.routers.extraction._results_path", _no_ner_file)

    payload = AutoApprovePayload(min_confidence=0.6, require_ai_pass=True)
    eligible = await _auto_approve_eligible(db_session, run_id, payload)
    assert len(eligible) == 1
    assert eligible[0]["text"] == "Judged pass"
