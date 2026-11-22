"""Check MARC work occurrences against source-qualified public titles."""

from dataclasses import asdict

from app.pipeline.wikidata_gila_facts import _live_claims, _reference_status, _text
from app.pipeline.wikidata_source_choices import _claim_values, candidates
from converter.wikidata.item_models import WikidataStatement
from converter.wikidata.property_mapping import nli_reference

_WORK_CLASSES = {"Q47461344", "Q7725634", "Q571"}
SUPPORTED_REASONS = {"public_target_requires_resolution", "work_target_identity_requires_review"}


def work_case(case):
    return (
        case.get("property") == "P1574"
        and case.get("reason") in SUPPORTED_REASONS
        and case.get("live_status") == "missing"
        and not case.get("expected_value")
    )


def work_source(raw, case):
    qualifiers = case.get("expected_qualifiers") or {}
    matches = [
        row for row in candidates(raw)
        if row["kind"] == "work_occurrence"
        and row["source_field"] == case.get("source_field")
        and _text(row["title"]) == _text(qualifiers.get("P1932"))
        and _text(row["folio"]) == _text(qualifiers.get("P958"))
        and _text(row["author"]) == _text(case.get("expected_author"))
    ]
    if len(matches) != 1 or matches[0]["scope_alignment_unproven"]:
        return None
    return matches[0]


def work_target(entity, cn, title, author=""):
    classes = {
        value["id"] for value in _claim_values(entity, "P31")
        if isinstance(value, dict) and "id" in value
    }
    if not classes.intersection(_WORK_CLASSES) or "Q87167" in classes:
        return False
    titles = [
        claim for claim in _live_claims(entity, "P1476")
        if _text(claim["value"]) == _text(title)
    ]
    if author:
        names = {_text(value).casefold() for value in _claim_values(entity, "P2093")}
        if names != {_text(author).casefold()} or _claim_values(entity, "P50"):
            return False
    return _reference_status(titles, cn, live=True) == "source_bound"


def same_occurrence(claim, qualifiers):
    return any(
        _text(q["value"]) and _text(q["value"]) == _text(qualifiers.get(q["property"]))
        for q in claim["qualifiers"]
        if q["property"] in {"P1932", "P958"}
    )


def exact_occurrence(claim, qualifiers):
    return sorted(
        (q["property"], _text(q["value"])) for q in claim["qualifiers"]
        if q["property"] in {"P1932", "P958"}
    ) == sorted((pid, _text(value)) for pid, value in qualifiers.items())


def work_statement(manuscript, target, case, occurrence):
    cn = case["control_number"]
    if (
        manuscript["id"] != case["qid"] or manuscript["id"] == target["id"]
        or set(_claim_values(manuscript, "P3959")) != {cn}
        or not work_target(target, cn, occurrence["title"], occurrence["author"])
    ):
        return None
    qualifiers = case["expected_qualifiers"]
    if any(
        claim["value"] == target["id"] or same_occurrence(claim, qualifiers)
        for claim in _live_claims(manuscript, "P1574")
    ):
        return None
    statement = asdict(WikidataStatement(
        "P1574", target["id"], "item",
        qualifiers=[{"property": pid, "value": value, "type": "string"}
                    for pid, value in qualifiers.items()],
        references=nli_reference(cn),
    ))
    statement["property"] = statement.pop("property_id")
    return statement
