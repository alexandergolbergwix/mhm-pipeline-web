"""Classify every audit result without granting permission for a public edit."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Literal

from app.pipeline.wikidata_gila_repairs import entity_digest

Disposition = Literal["verified", "manual_review", "repair_required", "not_checked"]

_POINTS = {
    1: "Labels and current shelfmarks",
    2: "Physical form and the accepted work model",
    3: "Work identities and manuscript links",
    4: "Ktiv membership",
    5: "People, roles, and authority evidence",
    6: "Contents and source folios",
    7: "Dates, places, languages, script, and extent",
    8: "Sources, bibliography, and inscriptions",
    9: "Complete corpus report and correction receipts",
}
_SEVERITY: dict[Disposition, int] = {
    "verified": 0,
    "manual_review": 1,
    "repair_required": 2,
    "not_checked": 3,
}


def _object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object.")
    return value


def _rows(value: object, name: str) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list.")
    return [_object(row, name) for row in value]


def _complete_entity(entity: Mapping[str, object]) -> bool:
    revision = entity.get("lastrevid")
    return (
        isinstance(revision, int)
        and not isinstance(revision, bool)
        and revision > 0
        and all(
            isinstance(entity.get(key), Mapping)
            for key in ("labels", "descriptions", "aliases", "claims", "sitelinks")
        )
    )


def _active_values(entity: Mapping[str, object], pid: str) -> set[str]:
    claims = _object(entity.get("claims", {}), "Entity claims")
    values = set()
    for claim in _rows(claims.get(pid, []), "Claims"):
        if claim.get("rank", "normal") not in {"normal", "preferred"}:
            continue
        snak = _object(claim.get("mainsnak"), "Claim mainsnak")
        if snak.get("snaktype") != "value":
            continue
        data = _object(snak.get("datavalue"), "Claim datavalue")
        value = data.get("value")
        if isinstance(value, str):
            values.add(value)
    return values


def _points_for_fact(fact: Mapping[str, object]) -> tuple[int, ...]:
    pid = fact.get("property")
    if pid == "P1574":
        return (3, 6) if fact.get("scope") == "content_occurrence" else (3,)
    if pid in {"P217", "description:en", "P3959"}:
        return (1,)
    if pid == "P361":
        return (4,)
    if pid in {"P50", "P2093", "P11603", "P3342"} or fact.get("scope") == "person_date_alignment":
        return (5,)
    if (
        pid in {"P571", "P407", "P9302", "P1104", "P1071", "P7153"}
        or fact.get("scope") == "place_role"
    ):
        return (7,)
    return (8,)


def _disposition(fact: Mapping[str, object]) -> Disposition:
    status = fact.get("live_status")
    if status == "not_checked":
        return "not_checked"
    if status == "source_evidence":
        return "manual_review"
    if status == "identity_review":
        return "manual_review"
    if status == "missing":
        expected = fact.get("expected_value")
        has_target = (
            isinstance(expected, str) and expected.startswith("Q") and expected[1:].isdigit()
        )
        if fact.get("property") == "P1574" and not has_target:
            return "manual_review"
        return "repair_required"
    if status != "present":
        raise ValueError(f"Unsupported live fact status: {status!r}.")
    provenance = fact.get("live_provenance")
    if provenance in {"missing", "invalid", "source_mismatch", "group_mismatch", "ambiguous_flat"}:
        return "repair_required"
    if provenance not in {"source_bound", "not_applicable"}:
        return "not_checked"
    return "verified"


def build_gila_completion_packet(
    audit: Mapping[str, object],
    live_entities: Mapping[str, object],
    *,
    repair_receipts: Iterable[Mapping[str, object]] = (),
) -> dict[str, object]:
    """Retain every case and require current public proof for corrected QIDs."""
    records = _rows(audit.get("records"), "Audit records")
    comparison = _object(audit.get("fact_comparison"), "Fact comparison")
    facts = _rows(comparison.get("facts"), "Source facts")
    case_rows: list[dict[str, object]] = []
    points: dict[int, list[Disposition]] = {point: [] for point in _POINTS}
    checked = []
    controls: set[str] = set()
    fact_ids: set[str] = set()
    complete_reads = True
    for record in records:
        cn = str(record.get("control_number") or "")
        if not cn or cn in controls:
            raise ValueError("Every source record needs one unique control number.")
        controls.add(cn)
        qid = str(record.get("qid") or "")
        raw = live_entities.get(qid)
        entity = _object(raw, "Public entity") if raw is not None else {}
        read = bool(
            qid
            and entity
            and "missing" not in entity
            and entity.get("id") == qid
            and _complete_entity(entity)
        )
        status: Disposition = "verified" if read else "not_checked"
        if read and _active_values(entity, "P3959") != {cn}:
            status = "repair_required"
        complete_reads = complete_reads and read
        points[9].append(status)
        checked.append(
            {
                "control_number": cn,
                "qid": qid,
                "revision": entity.get("lastrevid"),
                "disposition": status,
            }
        )
        if status != "verified":
            case_rows.append(
                {
                    "case_id": f"record:{cn}",
                    "control_number": cn,
                    "qid": qid,
                    "points": [9],
                    "disposition": status,
                    "reason": "public_record_identity_requires_review",
                }
            )
        for index, case in enumerate(_rows(record.get("cases", []), "Source cases")):
            point = case.get("point")
            if not isinstance(point, int) or point not in points:
                raise ValueError("A source case needs an email point from 1 to 9.")
            classification = case.get("classification")
            if classification not in {
                "absent_in_source",
                "identity_or_scope_uncertain",
                "present_not_transferred",
            }:
                raise ValueError("A source case needs a documented review class.")
            disposition: Disposition = (
                "repair_required"
                if classification == "present_not_transferred"
                else "manual_review"
            )
            points[point].append(disposition)
            case_rows.append(
                {
                    **dict(case),
                    "case_id": f"source:{cn}:{index}",
                    "control_number": cn,
                    "qid": qid,
                    "points": [point],
                    "disposition": disposition,
                }
            )
    if audit.get("record_count") != len(records):
        raise ValueError("The declared record count differs from the audit records.")
    for fact in facts:
        fact_id = str(fact.get("fact_id") or "")
        if not fact_id or fact_id in fact_ids or fact.get("control_number") not in controls:
            raise ValueError("Each fact needs a unique ID and a checked source record.")
        fact_ids.add(fact_id)
        raw_fact_entity = live_entities.get(str(fact.get("qid") or ""))
        current = _object(raw_fact_entity, "Fact target") if raw_fact_entity is not None else {}
        revision_matches = (
            bool(current)
            and "missing" not in current
            and fact.get("live_revision") == current.get("lastrevid")
            and _complete_entity(current)
        )
        disposition = _disposition(fact) if revision_matches else "not_checked"
        point_ids = _points_for_fact(fact)
        for point in point_ids:
            points[point].append(disposition)
        case_rows.append(
            {
                **dict(fact),
                "case_id": f"fact:{fact_id}",
                "points": list(point_ids),
                "disposition": disposition,
                "completion_reason": "audit_snapshot_revision_mismatch"
                if not revision_matches
                else "",
            }
        )

    by_control: dict[str, set[str]] = defaultdict(set)
    for qid, raw in live_entities.items():
        entity = _object(raw, "Public entity")
        if entity.get("id") != qid or "missing" in entity:
            continue
        for cn in _active_values(entity, "P3959") & controls:
            by_control[cn].add(qid)
    duplicates = [
        {"control_number": cn, "qids": sorted(qids), "disposition": "manual_review"}
        for cn, qids in sorted(by_control.items())
        if len(qids) > 1
    ]
    points[3].extend("manual_review" for _ in duplicates)
    for duplicate in duplicates:
        case_rows.append(
            {
                **duplicate,
                "case_id": f"duplicate:{duplicate['control_number']}",
                "points": [3],
                "reason": "duplicate_public_catalog_identity",
            }
        )
    historical = _rows(audit.get("historical_repair_proposals", []), "Historical proposals")
    for index, proposal in enumerate(historical):
        raw_target = live_entities.get(str(proposal.get("qid") or ""))
        target = _object(raw_target, "Historical target") if raw_target is not None else {}
        current_proposal = (
            _complete_entity(target)
            and target.get("id") == proposal.get("qid")
            and isinstance(proposal.get("revision"), int)
            and target.get("lastrevid") == proposal.get("revision")
            and proposal.get("entity_sha256") == entity_digest(target)
        )
        historical_status: Disposition = "repair_required" if current_proposal else "not_checked"
        points[8].append(historical_status)
        case_rows.append(
            {
                "case_id": f"historical:{proposal.get('qid')}:{index}",
                "qid": proposal.get("qid"),
                "live_revision": proposal.get("revision"),
                "points": [8],
                "disposition": historical_status,
                "reason": "exact_historical_proposal_requires_guarded_execution",
                "completion_reason": "" if current_proposal else "historical_snapshot_mismatch",
            }
        )

    corrected: set[str] = set()
    unproven = []
    for receipt in repair_receipts:
        if receipt.get("status") != "verified" or receipt.get("corrected") is not True:
            continue
        qid = str(receipt.get("qid") or "")
        raw = live_entities.get(qid)
        entity = _object(raw, "Receipt target") if raw is not None else {}
        revision = receipt.get("after_revision")
        if (
            _complete_entity(entity)
            and entity.get("id") == qid
            and isinstance(revision, int)
            and not isinstance(revision, bool)
            and entity.get("lastrevid") == revision
        ):
            corrected.add(qid)
        else:
            unproven.append(
                {
                    "qid": qid,
                    "receipt_after_revision": revision,
                    "live_revision": entity.get("lastrevid"),
                    "reason": "receipt_requires_fresh_verification",
                }
            )
    points[9].extend("not_checked" for _ in unproven)
    passage_rows = []
    for record in records:
        for passage in _rows(record.get("source_passages", []), "Source passages"):
            # Mapped claims do not prove that the rest of a note was consumed.
            row = {**passage, "qid": record.get("qid"), "disposition": "not_checked",
                   "status": "unchecked_passage", "reason": "passage_requires_exact_scope_and_public_reference_proof"}
            passage_rows.append(row)
    points[9].extend("not_checked" for _ in passage_rows)
    if any("source_passages" not in record for record in records):
        points[9].append("not_checked")
    points[2].append("manual_review")
    email_points = []
    for point, title in _POINTS.items():
        statuses = points[point]
        point_status: Disposition = (
            max(statuses, key=_SEVERITY.__getitem__) if statuses else "not_checked"
        )
        email_points.append(
            {
                "point": point,
                "title": title,
                "status": point_status,
                "counts": dict(Counter(statuses)),
            }
        )
    return {
        "mode": "read_only_completion_packet",
        "record_count": len(records),
        "all_records_read": complete_reads and bool(records),
        "all_requirements_verified": all(point["status"] == "verified" for point in email_points),
        "email_points": email_points,
        "requirement_coverage": {
            "version": 1,
            "public_reads": sum(row["disposition"] == "verified" for row in checked),
            "verified_claims": sum(row["disposition"] == "verified" for row in case_rows),
            "source_gaps": sum(row.get("classification") == "absent_in_source" for row in case_rows),
            "identity_reviews": sum(row.get("classification") == "identity_or_scope_uncertain" for row in case_rows),
            "pending_repairs": sum(row["disposition"] == "repair_required" for row in case_rows),
            "unchecked_passages": len(passage_rows),
            "records_without_passage_inventory": sum("source_passages" not in row for row in records),
            "model_decision": "The project uses separate physical manuscript and work items. Gila requested both classes on one item.",
        },
        "source_passages": passage_rows,
        "checked_manuscripts": checked,
        "cases": case_rows,
        "duplicate_catalog_identities": duplicates,
        "historical_repair_proposals": [dict(row) for row in historical],
        "corrected_qids": sorted(corrected),
        "receipts_requiring_verification": unproven,
        "disposition_counts": dict(Counter(str(row["disposition"]) for row in case_rows)),
        "executable": False,
    }
