"""Data structures shared by Wikidata item projection modules."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field, replace

from converter.wikidata.property_mapping import PRECISION_YEAR, FreshSourceReference


@dataclass
class WikidataStatement:
    """A single Wikidata claim with qualifiers and references."""

    property_id: str
    value: str | int | float | None
    value_type: str
    qualifiers: list[dict[str, object]] = field(default_factory=list)
    references: list[dict[str, object]] = field(default_factory=list)
    precision: int = PRECISION_YEAR
    language: str = "he"
    unit: str = ""
    rank: str = "normal"
    reference_blocks: list[list[dict[str, object]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.qualifiers = deepcopy(self.qualifiers)
        self.reference_blocks = deepcopy(self.reference_blocks)
        if isinstance(self.references, FreshSourceReference):
            source = self.references
            if not self.reference_blocks:
                self.references = []
                self.set_fresh_source(source)
            else:
                source._assert_unchanged()
                self.references = deepcopy(list(source))
        else:
            self.references = deepcopy(self.references)
        validate_statement_references(self)
        if self.reference_blocks:
            self.references = _deduplicate_reference_snaks(self.references)

    def __copy__(self) -> WikidataStatement:
        return replace(self)

    def set_fresh_source(self, source: FreshSourceReference) -> None:
        if not isinstance(source, FreshSourceReference):
            raise ValueError("Source reference is not freshly constructed")
        if self.references or self.reference_blocks:
            raise ValueError("Cannot replace existing reference evidence with a fresh source")
        source._assert_unchanged()
        self.references = _deduplicate_reference_snaks(list(source))
        self.reference_blocks = [deepcopy(list(source))] if source else []


def snak_identity(snak: dict[str, object]) -> str:
    """Compare native and Wikibase snaks without display-only labels."""
    aliases = {
        "wikibase-item": "item", "wikibase-entityid": "item",
        "externalid": "external-id",
    }

    def value_type(value: object) -> str:
        name = str(value or "string").lower()
        return aliases.get(name, name)

    property_id = snak.get("property") or snak.get("property_id")
    if snak.get("property") and snak.get("property_id") and (
        snak["property"] != snak["property_id"]
    ):
        raise ValueError("Conflicting reference property identifiers")
    types = [value_type(snak[key]) for key in ("type", "value_type", "datatype") if snak.get(key)]
    if len(set(types)) > 1:
        raise ValueError("Conflicting reference value types")
    kind = types[0] if types else "string"
    data = snak.get("datavalue")
    value = snak.get("value")
    if isinstance(data, dict) and "value" in data:
        expected_type = {
            "item": "wikibase-entityid", "time": "time", "quantity": "quantity",
            "monolingualtext": "monolingualtext", "string": "string", "url": "string",
            "external-id": "string",
        }.get(kind)
        if expected_type and data.get("type", expected_type) != expected_type:
            raise ValueError("Conflicting reference datavalue type")
        if "value" in snak and value != data["value"]:
            raise ValueError("Conflicting reference values")
        value = data["value"]
    if kind == "item" and isinstance(value, dict) and isinstance(value.get("id"), str):
        item_id = value["id"]
        numeric_id = value.get("numeric-id")
        if (value.get("entity-type", "item") != "item" or (
            numeric_id is not None and str(numeric_id) != item_id.removeprefix("Q")
        )):
            raise ValueError("Conflicting reference item identifiers")
        if set(value) - {"id", "numeric-id", "entity-type"}:
            raise ValueError("Unsupported reference item metadata")
        value = value["id"]
    ignored = {
        "property", "property_id", "type", "value_type", "datatype", "value",
        "datavalue", "snaktype", "property_label", "value_label", "label", "hash",
    }
    identity = {key: value for key, value in snak.items() if key not in ignored}
    if kind == "time":
        if isinstance(value, dict) and (
            set(value) <= {"time", "precision", "before", "after", "timezone", "calendarmodel"}
            and value.get("calendarmodel") == "http://www.wikidata.org/entity/Q1985727"
            and value.get("before", 0) == value.get("after", 0) == value.get("timezone", 0) == 0
            and "time" in value and "precision" in value
        ):
            if "precision" in identity and identity["precision"] != value["precision"]:
                raise ValueError("Conflicting reference time precision")
            identity["precision"] = value["precision"]
            value = value["time"]
        elif not isinstance(value, dict):
            identity.setdefault("precision", 11)
    identity.update({
        "property": property_id, "value_type": kind, "value": value,
        "snaktype": snak.get("snaktype", "value"),
    })
    return json.dumps(identity, sort_keys=True, ensure_ascii=False, allow_nan=False)


def validate_statement_references(statement: WikidataStatement) -> None:
    if not statement.reference_blocks:
        return
    flat = {snak_identity(snak) for snak in statement.references or []}
    blocks = {
        snak_identity(snak)
        for block in statement.reference_blocks
        for snak in block
    }
    if flat != blocks:
        raise ValueError("references and reference_blocks disagree")


def _deduplicate_reference_snaks(
    references: list[dict[str, object]],
) -> list[dict[str, object]]:
    seen: set[str] = set()
    result: list[dict[str, object]] = []
    for reference in references:
        key = snak_identity(reference)
        if key not in seen:
            seen.add(key)
            result.append(deepcopy(reference))
    return result


def matching_qualifiers(left: WikidataStatement, right: WikidataStatement) -> bool:
    return sorted(map(snak_identity, left.qualifiers or [])) == sorted(
        map(snak_identity, right.qualifiers or []),
    ) and left.rank == right.rank


def merge_statement_references(retained: WikidataStatement, removed: WikidataStatement) -> None:
    """Union complete source blocks while retaining the legacy flat projection."""
    validate_statement_references(retained)
    validate_statement_references(removed)
    blocks: list[list[dict[str, object]]] = []
    references = _deduplicate_reference_snaks(retained.references or [])
    flat_keys = {snak_identity(reference) for reference in references}
    block_keys: set[tuple[str, ...]] = set()
    for statement in (retained, removed):
        if not statement.reference_blocks and len(statement.references or []) > 1:
            raise ValueError("Ambiguous flat references; rebuild from source with reference blocks")
        source_blocks = statement.reference_blocks or (
            [statement.references] if statement.references else []
        )
        for block in source_blocks:
            for reference in block:
                key = snak_identity(reference)
                if key not in flat_keys:
                    references.append(deepcopy(reference))
                    flat_keys.add(key)
            key = tuple(sorted({snak_identity(reference) for reference in block}))
            if key not in block_keys:
                blocks.append(deepcopy(block))
                block_keys.add(key)
        for reference in statement.references or []:
            key = snak_identity(reference)
            if key not in flat_keys:
                references.append(deepcopy(reference))
                flat_keys.add(key)
    retained.references = references
    retained.reference_blocks = blocks


@dataclass
class WikidataItem:
    """A projected Wikidata item ready for validation and upload."""

    labels: dict[str, str] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, list[str]] = field(default_factory=dict)
    statements: list[WikidataStatement] = field(default_factory=list)
    existing_qid: str | None = None
    entity_type: str = ""
    # Optional semantic refinement retained for evaluator/export context.
    # Keep entity_type stable for existing upload/reconcile paths.
    semantic_type: str = ""
    local_id: str = ""
    records: list[str] = field(default_factory=list)
    authority_evidence: list[dict[str, object]] = field(default_factory=list)
    work_candidate_evidence: list[dict[str, object]] = field(default_factory=list)
    #: Set when an authority heading was refused as this person's label because it
    #: does not name the same person as the MARC heading (Rule W-166). Carries
    #: ``{marc, authority, reason}`` so the curator sees why the MARC form won.
    heading_mismatch: dict[str, object] | None = None
