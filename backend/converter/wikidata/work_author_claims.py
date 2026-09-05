"""Choose safe P50 or P2093 representations for projected work authors."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace

from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.semantic_policy import (
    DEFAULT_SEMANTIC_POLICY,
    SemanticQualityPolicy,
)

_QID_RE = re.compile(r"^Q[1-9][0-9]*$")


def _name_key(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text.strip().strip(" ,.;:/")).casefold()


def _statement_values(item: WikidataItem, property_id: str) -> set[str]:
    return {
        str(statement.value or "").strip()
        for statement in item.statements or []
        if statement.property_id == property_id and str(statement.value or "").strip()
    }


def _person_name_keys(item: WikidataItem) -> set[str]:
    names = set(item.labels.values())
    for aliases in item.aliases.values():
        names.update(aliases)
    names.update(_statement_values(item, "P1559"))
    return {key for name in names if (key := _name_key(name))}


def _identity_collision_members(
    people: list[WikidataItem],
    policy: SemanticQualityPolicy,
) -> set[str]:
    owners: dict[tuple[str, str], set[str]] = defaultdict(set)
    for person in people:
        for property_id in policy.strong_external_id_properties:
            for value in _statement_values(person, property_id):
                owners[(property_id, value)].add(person.local_id)
    return {
        entity_id
        for entity_ids in owners.values()
        if len(entity_ids) > 1
        for entity_id in entity_ids
    }


def _has_safe_identity(
    person: WikidataItem,
    policy: SemanticQualityPolicy,
) -> bool:
    if _QID_RE.fullmatch(str(person.existing_qid or "")):
        return True
    return any(
        _statement_values(person, property_id)
        for property_id in policy.strong_external_id_properties
    )


def _has_identity_warning(person: WikidataItem) -> bool:
    if person.heading_mismatch:
        return True
    return any(
        row.get("guard_flags") or row.get("rejection_reason")
        for row in person.authority_evidence or []
        if isinstance(row, dict)
    )


def _shares_source_record(work: WikidataItem, person: WikidataItem) -> bool:
    work_records = {str(value) for value in work.records or [] if value}
    person_records = {str(value) for value in person.records or [] if value}
    return bool(work_records and person_records and work_records & person_records)


def promote_safe_work_author_claims(
    items: Iterable[WikidataItem],
    *,
    policy: SemanticQualityPolicy = DEFAULT_SEMANTIC_POLICY,
) -> int:
    """Replace P2093 only when one safe source-linked person matches exactly."""
    corpus = list(items)
    people = [item for item in corpus if item.entity_type == "person"]
    collision_members = _identity_collision_members(people, policy)
    people_by_name: dict[str, list[WikidataItem]] = defaultdict(list)
    for person in people:
        if (
            not person.local_id
            or person.local_id in collision_members
            or not _has_safe_identity(person, policy)
            or _has_identity_warning(person)
        ):
            continue
        for name in _person_name_keys(person):
            people_by_name[name].append(person)

    changed = 0
    for work in corpus:
        if work.entity_type != "work":
            continue
        rewritten: list[WikidataStatement] = []
        existing_p50 = _statement_values(work, "P50")
        for statement in work.statements or []:
            if statement.property_id != "P2093":
                rewritten.append(statement)
                continue
            candidates = [
                person
                for person in people_by_name.get(_name_key(statement.value), [])
                if _shares_source_record(work, person)
            ]
            if len(candidates) != 1:
                rewritten.append(statement)
                continue
            person = candidates[0]
            target = (
                str(person.existing_qid)
                if _QID_RE.fullmatch(str(person.existing_qid or ""))
                else f"__LOCAL:{person.local_id}"
            )
            if target in existing_p50:
                changed += 1
                continue
            rewritten.append(replace(
                statement,
                property_id="P50",
                value=target,
                value_type="item",
            ))
            existing_p50.add(target)
            changed += 1
        work.statements = rewritten
    return changed
