from unittest.mock import AsyncMock

import pytest

from app.pipeline.wikidata_gila_exports import proven_claim_errors, verified_export
from app.pipeline.wikidata_reference_reader import ReferenceItemReader
from app.publication.reviewed_reference_scope import GILA_CREATOR_UID

CN = "990000543450205171"


def test_verified_export_keeps_only_verified_evidence_and_target_statement_ids():
    case = {
        "case_id": "fact:1", "disposition": "verified", "qid": "Q1",
        "verified_target_qid": "Q2", "verified_target_revision": 44,
        "control_number": CN, "property": "P1343",
        "verified_matches": [
            {"statement_id": "Q2$claim", "value": "Q5"},
            {"statement_id": "Q1$other", "value": "Q6"},
        ],
    }
    packet = {
        "checked_manuscripts": [
            {"control_number": CN, "qid": "Q1", "revision": 42, "disposition": "verified"},
            {"control_number": "unresolved", "disposition": "not_checked"},
        ],
        "cases": [case, {**case, "case_id": "fact:2", "disposition": "manual_review"}],
    }
    exported = verified_export(packet, [])
    assert len(exported["manuscripts"]) == 1
    assert exported["matched_statement_count"] == 1
    check, = exported["verified_checks"]
    assert check["qid"] == "Q2"
    assert check["revision"] == 44
    assert check["matches"] == [{"statement_id": "Q2$claim", "value": "Q5"}]


@pytest.mark.asyncio
async def test_proven_errors_require_exact_source_current_claim_and_project_creator(monkeypatch):
    cases = []
    entities = {}
    for qid in ("Q1", "Q2", "Q3", "Q4", "Q5"):
        row = {"statement_id": f"{qid}$claim", "value": "Q6",
               "error": "work_link_conflicts_with_reviewed_source"}
        cases.append({
            "qid": qid, "live_revision": 42, "control_number": CN, "property": "P1574",
            "source_field": "505", "expected_value": "Q7", "contradictions": [row],
        })
        claim = {"id": row["statement_id"], "references": [{"snaks": {
            "P3959": [{"datavalue": {"value": "other" if qid == "Q3" else CN}}],
        }}]}
        entities[qid] = {"id": qid, "lastrevid": 43 if qid == "Q4" else 42, "claims": {"P1574": [claim]}}
    creator = AsyncMock(side_effect={"Q1": GILA_CREATOR_UID, "Q2": 123, "Q5": None}.get)
    monkeypatch.setattr(ReferenceItemReader, "creator_uid", creator)
    report = await proven_claim_errors({"cases": cases}, entities, {})
    assert [row["statement_id"] for row in report["claims"]] == ["Q1$claim"]
    assert report["creator_checks_unavailable"] == 1
    assert {call.args[0] for call in creator.await_args_list} == {"Q1", "Q2", "Q5"}
