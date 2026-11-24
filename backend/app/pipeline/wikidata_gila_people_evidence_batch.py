"""Bind the reviewed evidence batch to the original audit case IDs."""

import json
from pathlib import Path

from app.pipeline.wikidata_gila_corpus_repair import corpus_inventory

_MANIFEST = Path(__file__).resolve().parents[1] / "data" / "gila_people_evidence_batch.json"
_SOURCE_FIELDS = ("control_number", "occurrence", "person_role", "qid")
_OPEN_REASON = {
    "author": "exact_author_identity_required",
    "scribe": "exact_scribe_identity_required",
    "former owner": "public_provenance_and_identity_required",
}


def evidence_batch(cases):
    """Return the current eligible inputs and a decision for every manifest row."""
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    expected = manifest["cases"]
    baseline = corpus_inventory([{**row, "points": [5]} for row in expected])
    if len(baseline) != len(expected):
        raise ValueError("The people evidence batch has duplicate case IDs.")
    current = {row["case_id"]: row for row in cases if row["points"] == [5]}
    selected = []
    for row in expected:
        case_id = row["case_id"]
        case = current.get(case_id)
        decision = baseline[case_id]
        decision.update(qid=row["target_qid"], person_role=row["person_role"])
        if not case:
            decision.update(status="needs_review", reason="case_absent_from_current_open_audit")
        elif any(row[key] != case.get(key) for key in _SOURCE_FIELDS):
            decision.update(status="needs_review", reason="source_occurrence_changed")
        else:
            decision.update(reason=_OPEN_REASON[row["person_role"]])
            selected.append(case)
    return selected, baseline, manifest["audit_id"]


def keep_reviewed_targets(plans, inventory):
    """Hold the entire item patch if any case has moved to another target."""
    confirmed = []
    for plan in plans:
        if all(inventory[case_id]["qid"] == plan.target["id"] for case_id in plan.case_ids):
            confirmed.append(plan)
        else:
            for case_id in plan.case_ids:
                inventory[case_id].update(status="needs_review", reason="reviewed_target_changed")
    return confirmed
