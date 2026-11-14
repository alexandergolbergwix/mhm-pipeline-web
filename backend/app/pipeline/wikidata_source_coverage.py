"""Account for source passages without confusing a projection with public proof."""

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy

from app.pipeline import marc_occurrences
from app.pipeline.wikidata_source_choices import candidates, digest, source_hash

PASSAGE_POINTS = {
    "500": (3, 5, 7, 8),
    "505": (3, 6),
    "581": (8,),
    "590": (7, 8),
    "740": (3, 6),
    "942": (1, 8),
}
PASSAGE_POINTS.update(
    {tag: (5,) for tag in ("100", "110", "111", "600", "700", "710", "711", "800", "880")}
)
PASSAGE_POINTS.update(
    {tag: (7,) for tag in ("008", "041", "046", "260", "264", "300", "340", "546", "751", "958")}
)
PASSAGE_POINTS.update({"001": (1, 9), "245": (1, 3), "246": (3,), "561": (8,), "541": (8,)})
REQUIREMENTS = (
    "Labels and current shelfmarks",
    "Physical manuscript and work model",
    "Work identities and links",
    "Ktiv membership",
    "People and roles",
    "Contents and folios",
    "Dates, places, language, script, and extent",
    "Sources and bibliography",
    "Complete source coverage and public receipts",
)


def source_passages(record: Mapping[str, object]) -> list[dict[str, object]]:
    source = deepcopy(dict(record))
    marc_occurrences.attach(source)
    record = source
    rows = []
    structured = marc_occurrences.occurrences(record)
    structured_tags = {row["tag"] for row in structured}
    for row in structured:
        tag = row["tag"]
        if tag not in PASSAGE_POINTS:
            continue
        rows.append(
            {
                "source_key": marc_occurrences.source_key(row),
                "source_field": tag,
                "source_text": str(row["data"])
                if "data" in row
                else " ".join(str(sf["value"]) for sf in row["subfields"]),
                "source_occurrence": row,
                "points": list(PASSAGE_POINTS[tag]),
            }
        )
    for key, value in record.items():
        tag = key[:3]
        if (
            tag not in PASSAGE_POINTS
            or tag in structured_tags
            or (key != tag and not key.startswith(tag + "$"))
        ):
            continue
        for index, text in enumerate(marc_occurrences.split_slots(value), 1):
            if text:
                rows.append(
                    {
                        "source_key": f"{key}:{index}",
                        "source_field": tag,
                        "source_text": text,
                        "points": list(PASSAGE_POINTS[tag]),
                        "association": "flattened_unproven",
                    }
                )
    cn = str(record.get("_control_number") or record.get("001") or "")
    fingerprint = source_hash(record)
    for row in rows:
        row.update(control_number=cn, source_hash=fingerprint)
        row["passage_id"] = digest(row)
    return rows


def source_coverage(
    record: Mapping[str, object],
    choices: object = None,
    *,
    item: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """A saved choice accounts for one candidate, never an entire MARC passage."""
    from app.pipeline.marc_ingest import prepare_record_for_pipeline
    from app.pipeline.wikidata_source_choices import validate_choices

    prepared = prepare_record_for_pipeline(dict(record))
    cn = str(prepared.get("_control_number") or "")
    valid = []
    stale = []
    for choice in choices if isinstance(choices, list) else []:
        if not isinstance(choice, dict) or choice.get("candidate", {}).get("control_number") != cn:
            continue
        try:
            valid.extend(validate_choices([choice], {cn: record}))
        except ValueError as exc:
            stale.append({"choice": choice, "reason": str(exc)})
    decisions = {p["candidate"]["candidate_key"]: p for p in valid}
    candidate_rows = []
    for row in candidates(record):
        proof = decisions.get(row["candidate_key"])
        candidate_rows.append(
            {
                **row,
                "status": (
                    "documented_unresolved"
                    if proof.get("decision") == "document"
                    else "rejected_match"
                    if proof.get("decision") == "reject"
                    else "reviewed_local"
                )
                if proof
                else "identity_review",
                "choice": proof,
            }
        )
        current_targets = []
        if item and row["kind"] == "work_occurrence":
            for statement in item.get("statements", []):
                if statement.get("property") != "P1574" and statement.get("property_id") != "P1574":
                    continue
                qualifiers = statement.get("qualifiers") or []
                titles = {
                    str(q.get("value", "")).strip()
                    for q in qualifiers
                    if (q.get("property") or q.get("property_id")) == "P1932"
                }
                folios = {
                    str(q.get("value", ""))
                    for q in qualifiers
                    if (q.get("property") or q.get("property_id")) == "P958"
                }
                target = statement.get("value")
                if (
                    str(row["title"]).strip() in titles
                    and folios == ({row["folio"]} if row.get("folio") else set())
                    and isinstance(target, str)
                ):
                    current_targets.append(target)
        candidate_rows[-1]["current_targets"] = sorted(set(current_targets))
    documented = {
        p["candidate"].get("passage_id"): p for p in valid if p.get("decision") == "document"
    }
    passages = [
        {
            **row,
            "status": "documented_unresolved"
            if row["passage_id"] in documented
            else "unchecked_passage",
            "decision": documented.get(row["passage_id"]),
        }
        for row in source_passages(record)
    ]
    gaps = []
    for field, point in (("shelfmark", 1), ("extent", 7)):
        raw_field = "942$z" if field == "shelfmark" else "300$a"
        source_present = any(marc_occurrences.split_slots(prepared.get(raw_field)))
        if (
            not source_present
            and not prepared.get(field)
            and not (field == "shelfmark" and prepared.get("holding_942_shelfmark"))
        ):
            gaps.append({"field": field, "point": point, "status": "source_gap"})
    counts = Counter(
        row["status"]
        for row in passages + [row for row in candidate_rows if row["kind"] != "passage"] + gaps
    )
    counts["stale_review"] = len(stale)
    requirements = [
        {"point": index, "title": title, "status": "requires_public_verification"}
        for index, title in enumerate(REQUIREMENTS, 1)
    ]
    requirements[1]["status"] = "documented_model_difference"
    for gap in gaps:
        requirements[gap["point"] - 1]["status"] = "source_gap"
    if prepared.get("holding_subfield_reviews") and requirements[0]["status"] != "source_gap":
        requirements[0]["status"] = "source_review"
    if prepared.get("person_subfield_reviews"):
        requirements[4]["status"] = "source_review"
    for candidate in candidate_rows:
        if candidate.get("scope_alignment_unproven"):
            requirements[5]["status"] = "source_review"
        if candidate["kind"] in {"work_occurrence", "catalog_reference"} and not candidate.get(
            "choice"
        ):
            requirements[7 if candidate["kind"] == "catalog_reference" else 2]["status"] = (
                "identity_review"
            )
    if stale or counts["unchecked_passage"]:
        requirements[8]["status"] = "unchecked_passages"
    from converter.wikidata.property_mapping import nli_reference

    source_url = next(
        (snak["value"] for snak in nli_reference(cn) if snak.get("property") == "P854"), ""
    )
    if not cn.isdigit():
        source_url = ""
    return {
        "control_number": cn,
        "source_url": source_url,
        "source_hash": source_hash(record),
        "passages": passages,
        "requirements": requirements,
        "original_occurrences_available": bool(marc_occurrences.occurrences(prepared)),
        "candidates": candidate_rows,
        "source_gaps": gaps,
        "stale_choices": stale,
        "record_status_notes": prepared.get("record_status_notes", []),
        "source_extent_text": prepared.get("300$a") or prepared.get("extent_text") or "",
        "catalog_references": prepared.get("catalog_references", []),
        "holding_subfield_reviews": prepared.get("holding_subfield_reviews", []),
        "person_subfield_reviews": prepared.get("person_subfield_reviews", []),
        "counts": dict(counts),
        "all_passages_verified": False,
        "model_decision": {
            "point": 2,
            "status": "documented_model_difference",
            "physical_manuscript_and_work": "separate_items",
        },
        "public_verification": "requires_current_read_and_exact_claim_references",
    }
