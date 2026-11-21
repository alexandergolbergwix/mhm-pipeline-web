"""Exact source and target evidence for the Ḥizzuq Emunah author repair."""

from collections.abc import Mapping

WORK_QID = "Q141175530"
WORK_LOCAL_ID = "work:חזוק_אמונה"
AUTHOR_QID = "Q168171"
AUTHOR_HEADING = "יצחק בן אברהם מטרוקי"
WORK_SOURCE = "990000621640205171"
MANUSCRIPT_QID = "Q141747170"
MANUSCRIPT_SOURCE = "990000885680205171"
REPAIR_MARKER = "publication_gila_troki_author"


def author_statement(item):
    if (
        item.get("local_id") != WORK_LOCAL_ID
        or item.get("entity_type") != "work"
        or item.get("existing_qid") not in (None, "", WORK_QID)
        or not {WORK_SOURCE, MANUSCRIPT_SOURCE} <= set(item.get("records") or [])
        or item.get("rebuild_required")
        or any(issue.get("severity") == "error" for issue in item.get("validation_issues", []))
    ):
        return None
    statements = [
        row for row in item.get("statements", [])
        if row.get("property") in ("P50", "P2093") and row.get("rank") != "deprecated"
    ]
    if len(statements) != 1:
        return None
    statement = statements[0]
    if (
        statement.get("property") != "P50" or statement.get("value") != AUTHOR_QID
        or not has_author_evidence(
            statement.get("qualifiers", []), statement.get("reference_blocks", []),
        )
    ):
        return None
    return statement


def has_author_evidence(qualifiers, reference_blocks) -> bool:
    return any(
        (snak.get("property") or snak.get("property_id")) == "P1932"
        and snak.get("value") == AUTHOR_HEADING
        for snak in qualifiers
    ) and any(
        {(snak.get("property") or snak.get("property_id"), snak.get("value"))
         for snak in block if isinstance(snak.get("value"), str)}
        >= {("P3959", WORK_SOURCE), ("P248", "Q118384267")}
        for block in reference_blocks
    )


def _active_claims(entity, prop):
    return [
        row for row in entity.get("claims", {}).get(prop, []) if row.get("rank") != "deprecated"
    ]


def _snak_value(snak):
    value = snak.get("datavalue", {}).get("value")
    return value.get("id") if isinstance(value, Mapping) else value


def _values(rows):
    return {_snak_value(row.get("mainsnak", {})) for row in rows}


def _references_record(claim, control_number):
    return any(
        control_number in {_snak_value(snak) for snak in block["snaks"].get("P3959", [])}
        and "Q118384267" in {_snak_value(snak) for snak in block["snaks"].get("P248", [])}
        for block in claim.get("references", [])
    )


def work_matches(entity, *, require_missing=False):
    types = _active_claims(entity, "P31")
    authors = _values(_active_claims(entity, "P50"))
    names = _active_claims(entity, "P2093")
    return (
        entity.get("id") == WORK_QID
        and entity.get("labels", {}).get("he", {}).get("value") == "חזוק אמונה"
        and _values(types) == {"Q47461344"}
        and any(_references_record(claim, WORK_SOURCE) for claim in types)
        and _values(names) == {AUTHOR_HEADING}
        and any(_references_record(claim, WORK_SOURCE) for claim in names)
        and (not authors if require_missing else authors <= {AUTHOR_QID})
    )


def manuscript_links_work(entity):
    return (
        entity.get("id") == MANUSCRIPT_QID
        and _values(_active_claims(entity, "P3959")) == {MANUSCRIPT_SOURCE}
        and any(
            _snak_value(claim.get("mainsnak", {})) == WORK_QID
            and _references_record(claim, MANUSCRIPT_SOURCE)
            for claim in _active_claims(entity, "P1574")
        )
    )


def author_matches(entity):
    return (
        entity.get("id") == AUTHOR_QID
        and _values(_active_claims(entity, "P31")) == {"Q5"}
        and _values(_active_claims(entity, "P8189")) == {"987007297464205171"}
        and _values(_active_claims(entity, "P214")) == {"67288238"}
    )
