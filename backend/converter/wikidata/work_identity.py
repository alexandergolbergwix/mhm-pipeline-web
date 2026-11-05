"""Shared author and source evidence for work reconciliation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

WorkComparison = Literal["match", "different", "uncertain"]
_QID = re.compile(r"Q[1-9]\d*")
_CONTROL = re.compile(r"(?<!\d)990\d{15}(?!\d)")


def author_name_key(value: object) -> str:
    return " ".join(str(value or "").strip().rstrip(" ,.;:/-").split()).casefold()


@dataclass(frozen=True)
class WorkIdentity:
    author_qids: frozenset[str] = frozenset()
    author_names: frozenset[str] = frozenset()
    source_records: frozenset[str] = frozenset()
    source_scoped: bool = False
    unresolved_authors: bool = False

    def cache_key(self, title: str, language: str) -> str:
        qids = ",".join(sorted(self.author_qids))
        names = sorted(self.author_names)
        key = f"work:{language}:{title}:{qids}"
        if len(names) == 1:
            return f"{key}:{names[0]}"
        if names:
            return f"{key}:{names!r}"
        return f"{key}:unresolved-author" if self.unresolved_authors else key


def reference_control_numbers(references: object) -> frozenset[str]:
    if references is None:
        return frozenset()
    if not isinstance(references, list):
        raise ValueError("Work references must be a list.")
    records: set[str] = set()
    for reference in references:
        if not isinstance(reference, Mapping) or not isinstance(reference.get("snaks"), Mapping):
            raise ValueError("A work reference must contain a snaks object.")
        snaks = reference["snaks"]
        for pid in ("P3959", "P854"):
            sources = snaks.get(pid) or []
            if not isinstance(sources, list):
                raise ValueError("Reference snaks must be lists.")
            for source in sources:
                if not isinstance(source, Mapping):
                    raise ValueError("A reference snak must be an object.")
                datavalue = source.get("datavalue") or {}
                if not isinstance(datavalue, Mapping):
                    raise ValueError("A reference datavalue must be an object.")
                value = datavalue.get("value")
                if value is None:
                    continue
                if not isinstance(value, str):
                    raise ValueError("Source identifiers and URLs must be strings.")
                if pid == "P3959":
                    records.add(value.strip())
                else:
                    host = urlsplit(value).hostname or ""
                    if host == "nli.org.il" or host.endswith(".nli.org.il"):
                        records.update(_CONTROL.findall(value))
    return frozenset(records)


def work_identity_for_item(item: object) -> WorkIdentity:
    qids: set[str] = set()
    names: set[str] = set()
    has_author = False
    for statement in getattr(item, "statements", []) or []:
        pid = getattr(statement, "property_id", "")
        value = getattr(statement, "value", None)
        if pid == "P50":
            has_author = True
            if isinstance(value, Mapping):
                value = value.get("id")
            if isinstance(value, str) and _QID.fullmatch(value):
                qids.add(value)
            for qualifier in getattr(statement, "qualifiers", []) or []:
                if qualifier.get("property") == "P1932" and qualifier.get("value"):
                    names.add(author_name_key(qualifier["value"]))
        elif pid == "P2093" and value:
            has_author = True
            names.add(author_name_key(value))
    evidence = getattr(item, "work_candidate_evidence", []) or []
    if has_author and not names:
        candidates: set[str] = set()
        for row in evidence:
            if row.get("author_name"):
                candidates.add(author_name_key(row["author_name"]))
            elif row.get("source_field") == "100/245":
                source = str(row.get("source_text") or "")
                if " — " in source:
                    candidates.add(author_name_key(source.rsplit(" — ", 1)[1]))
        if len(candidates) == 1:
            names = candidates
    return WorkIdentity(
        frozenset(qids), frozenset(names),
        frozenset(str(cn).strip().strip('"') for cn in getattr(item, "records", []) or []),
        any(row.get("identity_scope") == "record" for row in evidence),
        has_author and not qids and not names,
    )


def work_identity_from_entity(entity: Mapping[str, object]) -> WorkIdentity:
    claims = entity.get("claims") or {}
    if not isinstance(claims, Mapping):
        raise ValueError("Work claims must be an object.")
    qids: set[str] = set()
    names: set[str] = set()
    records: set[str] = set()
    has_author = False
    unknown_author = False
    for pid, statements in claims.items():
        if not isinstance(statements, list):
            raise ValueError("Work claim collections must be lists.")
        for statement in statements:
            if not isinstance(statement, Mapping):
                raise ValueError("A work claim must be an object.")
            snak = statement.get("mainsnak") or {}
            if not isinstance(snak, Mapping):
                raise ValueError("A work mainsnak must be an object.")
            datavalue = snak.get("datavalue") or {}
            if not isinstance(datavalue, Mapping):
                raise ValueError("A work datavalue must be an object.")
            value = datavalue.get("value")
            if pid in {"P50", "P2093"}:
                has_author = True
            if pid == "P50" and isinstance(value, Mapping):
                qid = value.get("id")
                if isinstance(qid, str) and _QID.fullmatch(qid):
                    qids.add(qid)
                else:
                    unknown_author = True
            elif pid == "P2093" and isinstance(value, str):
                names.add(author_name_key(value))
            elif pid in {"P50", "P2093"}:
                unknown_author = True
            records.update(reference_control_numbers(statement.get("references")))
    return WorkIdentity(
        frozenset(qids), frozenset(names), frozenset(records),
        unresolved_authors=unknown_author or (has_author and not qids and not names),
    )


def work_identity_from_metadata(metadata: Mapping[str, object]) -> WorkIdentity:
    def strings(key: str) -> frozenset[str]:
        values = metadata.get(key) or []
        if not isinstance(values, (list, tuple, set, frozenset)) or any(
            not isinstance(value, str) for value in values
        ):
            raise ValueError(f"Work metadata {key} must contain strings.")
        return frozenset(value for value in values if isinstance(value, str))

    legacy = strings("authors")
    qids = strings("author_qids") if "author_qids" in metadata else frozenset(
        value for value in legacy if _QID.fullmatch(value)
    )
    if any(not _QID.fullmatch(value) for value in qids):
        raise ValueError("Work author QIDs must be public item identifiers.")
    names = strings("author_names") if "author_names" in metadata else frozenset(
        value for value in legacy if not _QID.fullmatch(value)
    )
    return WorkIdentity(
        qids, frozenset(author_name_key(value) for value in names), strings("source_records"),
        unresolved_authors=bool(metadata.get("unresolved_authors")),
    )


def compare_work_identity(
    proposed: WorkIdentity, existing: WorkIdentity, *, strong_identifier: bool = False,
) -> WorkComparison:
    if proposed.unresolved_authors or (existing.unresolved_authors and (
        not strong_identifier or existing.author_qids or existing.author_names
    )):
        return "uncertain"
    if proposed.source_scoped and (
        not proposed.source_records or proposed.source_records != existing.source_records
    ):
        return "uncertain"
    if proposed.author_qids and existing.author_qids - proposed.author_qids:
        if proposed.author_qids.isdisjoint(existing.author_qids):
            if proposed.author_names & existing.author_names:
                return "uncertain"
            return "different"
        return "uncertain"
    if not strong_identifier and proposed.author_qids and existing.author_qids and (
        proposed.author_qids != existing.author_qids
    ):
        return "uncertain"
    if proposed.author_names and existing.author_names - proposed.author_names:
        return "uncertain"
    if (
        not strong_identifier and not proposed.author_qids and not existing.author_qids
        and proposed.author_names and existing.author_names
        and proposed.author_names != existing.author_names
    ):
        return "uncertain"
    if proposed.author_qids and existing.author_names and not proposed.author_names:
        return "uncertain"
    if proposed.author_names and existing.author_qids and not proposed.author_qids:
        return "uncertain"
    proposed_authors = bool(proposed.author_qids or proposed.author_names)
    existing_authors = bool(existing.author_qids or existing.author_names)
    if not strong_identifier and proposed_authors != existing_authors:
        return "uncertain"
    return "match"
