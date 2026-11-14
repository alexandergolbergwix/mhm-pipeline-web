"""A completion packet must retain gaps and require current public evidence."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.pipeline.wikidata_gila_completion import build_gila_completion_packet
from app.pipeline.wikidata_gila_repairs import entity_digest
from scripts.export_gila_completion import export_packet


def _entity(qid: str = "Q1", revision: int = 2) -> dict[str, object]:
    return {
        "id": qid,
        "lastrevid": revision,
        "labels": {},
        "descriptions": {},
        "aliases": {},
        "sitelinks": {},
        "claims": {
            "P3959": [
                {
                    "rank": "normal",
                    "mainsnak": {"snaktype": "value", "datavalue": {"value": "source-1"}},
                }
            ]
        },
    }


def _fact(**changes: object) -> dict[str, object]:
    return {
        "fact_id": "fact-1",
        "control_number": "source-1",
        "qid": "Q1",
        "property": "P1574",
        "scope": "content_occurrence",
        "expected_value": None,
        "live_status": "missing",
        "live_revision": 2,
        "live_provenance": "no_matching_claim",
        **changes,
    }


def _audit(
    facts: list[dict[str, object]], cases: list[dict[str, object]] | None = None
) -> dict[str, object]:
    return {
        "record_count": 1,
        "records": [{"control_number": "source-1", "qid": "Q1", "cases": cases or []}],
        "fact_comparison": {"facts": facts},
        "historical_repair_proposals": [],
    }


def test_an_unknown_work_needs_identity_review_instead_of_automatic_creation() -> None:
    result = build_gila_completion_packet(_audit([_fact()]), {"Q1": _entity()})
    assert result["cases"][0]["disposition"] == "manual_review"
    assert result["corrected_qids"] == []
    assert result["all_requirements_verified"] is False
    assert result["executable"] is False


def test_a_missing_known_work_remains_a_supported_correction() -> None:
    result = build_gila_completion_packet(_audit([_fact(expected_value="Q123")]), {"Q1": _entity()})
    assert result["cases"][0]["disposition"] == "repair_required"
    assert result["cases"][0]["points"] == [3, 6]


def test_a_present_value_with_the_wrong_source_is_not_verified() -> None:
    result = build_gila_completion_packet(
        _audit([_fact(live_status="present", live_provenance="source_mismatch")]), {"Q1": _entity()}
    )
    assert result["cases"][0]["disposition"] == "repair_required"


def test_an_audit_for_an_older_public_revision_requires_another_comparison() -> None:
    result = build_gila_completion_packet(
        _audit([_fact(live_status="present", live_provenance="source_bound")]),
        {"Q1": _entity(revision=3)},
    )
    assert result["cases"][0]["disposition"] == "not_checked"
    assert result["cases"][0]["completion_reason"] == "audit_snapshot_revision_mismatch"


def test_absent_source_data_remains_an_explicit_case_for_its_email_point() -> None:
    audit = _audit(
        [],
        [
            {
                "point": 7,
                "classification": "absent_in_source",
                "field": "300$a",
                "reason": "no_extent",
            }
        ],
    )
    result = build_gila_completion_packet(audit, {"Q1": _entity()})
    assert result["cases"][0]["classification"] == "absent_in_source"
    assert result["cases"][0]["disposition"] == "manual_review"
    assert result["email_points"][6]["status"] == "manual_review"


def test_a_second_public_manuscript_with_the_same_source_id_requires_identity_review() -> None:
    result = build_gila_completion_packet(_audit([]), {"Q1": _entity(), "Q2": _entity("Q2")})
    assert result["duplicate_catalog_identities"] == [
        {"control_number": "source-1", "qids": ["Q1", "Q2"], "disposition": "manual_review"}
    ]
    assert result["email_points"][2]["status"] == "manual_review"


def test_a_redirect_response_does_not_create_a_false_duplicate_manuscript() -> None:
    redirected = {**_entity(), "redirects": {"from": "Q2", "to": "Q1"}}
    result = build_gila_completion_packet(_audit([]), {"Q1": _entity(), "Q2": redirected})
    assert result["duplicate_catalog_identities"] == []


def test_correction_receipts_need_the_exact_current_entity_and_revision() -> None:
    receipts = [
        {"qid": "Q1", "status": "verified", "corrected": True, "after_revision": 2},
        {"qid": "Q2", "status": "verified", "corrected": True, "after_revision": 1},
        {"qid": "Q3", "status": "uncertain", "corrected": False, "after_revision": 2},
    ]
    result = build_gila_completion_packet(
        _audit([]), {"Q1": _entity(), "Q2": _entity("Q2")}, repair_receipts=receipts
    )
    assert result["corrected_qids"] == ["Q1"]
    assert result["receipts_requiring_verification"] == [
        {
            "qid": "Q2",
            "receipt_after_revision": 1,
            "live_revision": 2,
            "reason": "receipt_requires_fresh_verification",
        }
    ]


def test_the_packet_rejects_a_duplicate_source_fact_id() -> None:
    with pytest.raises(ValueError, match="unique ID"):
        build_gila_completion_packet(_audit([_fact(), _fact()]), {"Q1": _entity()})


def test_an_unchecked_manuscript_prevents_a_complete_corpus_read() -> None:
    result = build_gila_completion_packet(_audit([_fact()]), {})
    assert result["all_records_read"] is False
    assert result["checked_manuscripts"][0]["disposition"] == "not_checked"


def test_a_partial_entity_response_cannot_create_a_false_missing_description() -> None:
    partial = _entity()
    partial.pop("descriptions")
    audit = _audit([_fact(property="description:en", expected_value="Manuscript")])
    result = build_gila_completion_packet(audit, {"Q1": partial})
    assert result["all_records_read"] is False
    assert all(row["disposition"] == "not_checked" for row in result["cases"])


def test_a_historical_repair_appears_in_the_correction_inventory() -> None:
    audit = _audit([])
    audit["historical_repair_proposals"] = [
        {
            "qid": "Q1",
            "revision": 2,
            "entity_sha256": entity_digest(_entity()),
        }
    ]
    result = build_gila_completion_packet(audit, {"Q1": _entity()})
    assert result["disposition_counts"] == {"repair_required": 1}
    assert result["cases"][0]["case_id"] == "historical:Q1:0"
    assert result["email_points"][7]["status"] == "repair_required"


def test_a_stale_historical_proposal_cannot_enter_the_ready_correction_inventory() -> None:
    audit = _audit([])
    audit["historical_repair_proposals"] = [
        {
            "qid": "Q1",
            "revision": 2,
            "entity_sha256": entity_digest(_entity()),
        }
    ]
    result = build_gila_completion_packet(audit, {"Q1": _entity(revision=3)})
    assert result["cases"][0]["disposition"] == "not_checked"
    assert result["cases"][0]["completion_reason"] == "historical_snapshot_mismatch"


def test_the_export_keeps_every_case_and_writes_a_manifest(tmp_path: Path) -> None:
    audit = _audit([_fact(), _fact(fact_id="fact-2", expected_value="Q123")])
    before = deepcopy(audit)
    source_path, snapshot_path = tmp_path / "audit.json", tmp_path / "snapshot.json"
    source_path.write_text(json.dumps(audit))
    snapshot_path.write_text(json.dumps({"entities": {"Q1": _entity()}}))
    output = tmp_path / "packet"
    manifest = export_packet(source_path, snapshot_path, [], output)
    assert manifest["disposition_counts"] == {"manual_review": 1, "repair_required": 1}
    assert manifest["corrected_qids"] == []
    assert (output / "manual-review.csv").read_text().count("fact:fact-1") == 1
    assert (output / "supported-corrections.csv").read_text().count("fact:fact-2") == 1
    assert "completion.json" in manifest["outputs"]
    assert audit == before
