"""Preserve scoped colophons when the canonical source repeats an exact wrapper."""

from collections.abc import Mapping

from app.pipeline.wikidata_gila_repairs import is_source_exact_colophon_duplicate
from converter.wikidata.item_models import (
    WikidataItem,
    WikidataStatement,
    merge_statement_references,
    validate_statement_references,
)
from converter.wikidata.work_identity import reference_control_numbers


def _value(snak: Mapping[str, object]) -> object:
    data = snak.get("datavalue")
    return snak.get("value", data.get("value") if isinstance(data, Mapping) else None)


def _claim_witness(statement: WikidataStatement) -> dict[str, object]:
    """Expose only the fields used by the shared source-equivalence guard."""
    validate_statement_references(statement)
    qualifiers: dict[str, list[dict[str, object]]] = {}
    for qualifier in statement.qualifiers:
        pid = str(qualifier.get("property") or qualifier.get("property_id") or "")
        value = _value(qualifier)
        kind = qualifier.get("type") or qualifier.get("value_type")
        public = qualifier.get("value_namespace", "wikidata") in {None, "", "wikidata"}
        if kind in {"item", "wikibase-item", "wikibase-entityid"}:
            value = {"id": value} if public else None
        qualifiers.setdefault(pid, []).append({
            "snaktype": qualifier.get("snaktype", "value"), "datavalue": {"value": value},
        })
    references = []
    blocks = statement.reference_blocks or ([statement.references] if statement.references else [])
    for block in blocks:
        snaks: dict[str, list[dict[str, object]]] = {}
        for snak in block:
            pid = str(snak.get("property") or snak.get("property_id") or "")
            if pid in {"P3959", "P854"}:
                snaks.setdefault(pid, []).append({"datavalue": {"value": _value(snak)}})
        references.append({"snaks": snaks})
    return {
        "rank": statement.rank, "qualifiers": qualifiers, "references": references,
        "mainsnak": {
            "property": "P1684", "snaktype": "value", "datatype": statement.value_type,
            "datavalue": {"type": "monolingualtext", "value": {
                "text": statement.value, "language": statement.language,
            }},
        },
    }


def collapse_source_colophon_wrappers(
    item: WikidataItem, *, source_text: str, control_number: str,
) -> int:
    """Remove only an unscoped wrapper with one source-exact scoped counterpart."""
    if item.entity_type != "manuscript" or set(item.records) != {control_number}:
        return 0
    candidates = [s for s in item.statements if s.property_id == "P1684"]
    witnesses = {id(s): _claim_witness(s) for s in candidates}
    removed: set[int] = set()
    for old in candidates:
        old_claim = witnesses[id(old)]
        if reference_control_numbers(old_claim["references"]) != {control_number}:
            continue
        matches = [
            kept for kept in candidates if kept is not old
            and reference_control_numbers(witnesses[id(kept)]["references"]) == {control_number}
            and is_source_exact_colophon_duplicate(old_claim, witnesses[id(kept)], source_text)
        ]
        if len(matches) == 1:
            merge_statement_references(matches[0], old)
            removed.add(id(old))
    item.statements = [s for s in item.statements if id(s) not in removed]
    return len(removed)
