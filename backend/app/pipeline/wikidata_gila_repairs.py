"""Read-only, revision-bound proposals for historical Wikidata defects."""

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy

from converter.wikidata.work_identity import reference_control_numbers


def entity_digest(entity: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(entity, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _objects(value: object, name: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"{name} must contain objects.")
    return list(value)


def _reference_identity(reference: Mapping[str, object]) -> str:
    snaks = reference.get("snaks")
    if not isinstance(snaks, Mapping) or not snaks:
        raise ValueError("A reference must contain snaks.")
    normalized = {
        pid: sorted(
            json.dumps({key: value for key, value in snak.items() if key != "hash"}, sort_keys=True)
            for snak in _objects(rows, "Reference snaks")
        )
        for pid, rows in snaks.items()
    }
    return json.dumps(normalized, sort_keys=True)


def _distinct_references(value: object) -> list[dict[str, object]]:
    references = _objects(value, "References")
    seen: set[str] = set()
    kept = []
    for reference in references:
        identity = _reference_identity(reference)
        if identity not in seen:
            seen.add(identity)
            kept.append(deepcopy(reference))
    return kept


def _text(claim: Mapping[str, object]) -> str:
    snak = claim.get("mainsnak")
    if not isinstance(snak, Mapping):
        return ""
    datavalue = snak.get("datavalue")
    if not isinstance(datavalue, Mapping):
        return ""
    value = datavalue.get("value")
    return str(value.get("text") or "") if isinstance(value, Mapping) else ""


def _has_colophon_scope(claim: Mapping[str, object]) -> bool:
    if claim.get("rank") == "deprecated":
        return False
    qualifiers = claim.get("qualifiers") or {}
    if not isinstance(qualifiers, Mapping):
        raise ValueError("Claim qualifiers must be an object.")
    roles = []
    for snak in _objects(qualifiers.get("P3831", []), "Role qualifiers"):
        data = snak.get("datavalue")
        value = data.get("value") if isinstance(data, Mapping) else None
        if snak.get("snaktype") == "value" and isinstance(value, Mapping):
            roles.append(value.get("id"))
    folios = _objects(qualifiers.get("P7416") or qualifiers.get("P958") or [], "Folio qualifiers")
    for folio in folios:
        data = folio.get("datavalue")
        value = data.get("value") if isinstance(data, Mapping) else None
        if folio.get("snaktype") != "value" or not isinstance(value, str) or not value.strip():
            return False
    return "Q372474" in roles and bool(folios)


def _inscription_language(claim: Mapping[str, object]) -> str:
    snak = claim.get("mainsnak")
    if (
        not isinstance(snak, Mapping)
        or snak.get("snaktype") != "value"
        or snak.get("datatype") != "monolingualtext"
    ):
        return ""
    data = snak.get("datavalue")
    value = (
        data.get("value")
        if isinstance(data, Mapping) and data.get("type") == "monolingualtext"
        else None
    )
    language = value.get("language") if isinstance(value, Mapping) else None
    return language if isinstance(language, str) and language else ""


def is_source_exact_colophon_duplicate(
    old: Mapping[str, object], kept: Mapping[str, object], source: str,
) -> bool:
    old_text = _text(old)
    old_sources = reference_control_numbers(old.get("references"))
    kept_sources = reference_control_numbers(kept.get("references"))
    return bool(
        source
        and isinstance(old.get("mainsnak"), Mapping)
        and old["mainsnak"].get("property") == "P1684"
        and isinstance(kept.get("mainsnak"), Mapping)
        and kept["mainsnak"].get("property") == "P1684"
        and _text(kept) == source
        and _has_colophon_scope(kept)
        and not old.get("qualifiers")
        and old_text != source
        and old_text.replace('""', '"') in {source, f'"{source}"', f'"{source}".'}
        and _inscription_language(old)
        and _inscription_language(old) == _inscription_language(kept)
        and old_sources
        and old_sources <= kept_sources
        and old.get("rank", "normal") == kept.get("rank", "normal")
        and old.get("rank") != "deprecated"
    )


def build_gila_repair_proposals(
    entities: Mapping[str, object],
    *,
    colophons_by_qid: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    """Retain exact before/after claims; this function performs no writes."""
    proposals = []
    for qid, entity in entities.items():
        if not isinstance(entity, Mapping):
            raise ValueError("A live entity must be an object.")
        if "missing" in entity:
            continue
        claims = entity.get("claims", {})
        if not isinstance(claims, Mapping):
            raise ValueError("Live claims must be an object.")
        changes = []
        for pid, rows in claims.items():
            for claim in _objects(rows, "Claims"):
                references = _objects(claim.get("references", []), "References")
                distinct = _distinct_references(references)
                if len(distinct) == len(references):
                    continue
                claim_id = claim.get("id")
                if not isinstance(claim_id, str) or not claim_id.startswith(f"{qid}$"):
                    raise ValueError("A repair requires an exact claim ID.")
                after = deepcopy(claim)
                after["references"] = distinct
                changes.append(
                    {
                        "kind": "deduplicate_exact_references",
                        "property": pid,
                        "claim_id": claim_id,
                        "before": deepcopy(claim),
                        "after": after,
                        "duplicate_blocks": len(references) - len(distinct),
                    }
                )
        source_text = (colophons_by_qid or {}).get(qid, "")
        inscriptions = _objects(claims.get("P1684", []), "Inscriptions")
        correct = [
            claim
            for claim in inscriptions
            if _text(claim) == source_text and _has_colophon_scope(claim)
        ]
        if source_text and len(correct) == 1:
            kept = correct[0]
            for old in inscriptions:
                if not is_source_exact_colophon_duplicate(old, kept, source_text):
                    continue
                existing = next(
                    (change for change in changes if change["claim_id"] == kept["id"]), None
                )
                after = deepcopy(existing["after"] if existing else kept)
                after["references"] = _distinct_references(
                    [
                        *_objects(after.get("references", []), "References"),
                        *_objects(old.get("references", []), "References"),
                    ]
                )
                changes[:] = [
                    change
                    for change in changes
                    if change["claim_id"] not in {old["id"], kept["id"]}
                ]
                if after != kept:
                    changes.append(
                        {
                            "kind": "preserve_colophon_references",
                            "property": "P1684",
                            "claim_id": kept["id"],
                            "before": deepcopy(kept),
                            "after": after,
                        }
                    )
                changes.append(
                    {
                        "kind": "remove_duplicate_colophon",
                        "property": "P1684",
                        "claim_id": old["id"],
                        "before": deepcopy(old),
                        "after": None,
                        "retained_claim_id": kept["id"],
                        "source_text": source_text,
                    }
                )
        if changes:
            revision = entity.get("lastrevid")
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
                raise ValueError("A repair requires a positive entity revision.")
            proposals.append(
                {
                    "qid": qid,
                    "revision": revision,
                    "entity_sha256": entity_digest(entity),
                    "action": "review_exact_claim_changes",
                    "changes": changes,
                    "requires_review": True,
                    "requires_authorization": True,
                    "requires_fresh_read": True,
                    "executable": False,
                }
            )
    return proposals


def validate_gila_repair_proposal(
    proposal: Mapping[str, object],
    entity: Mapping[str, object],
    *,
    prior_outcome: str = "not_sent",
) -> None:
    """Validate evidence only; ownership and write permission remain separate."""
    if prior_outcome != "not_sent":
        raise ValueError(
            "An applied or uncertain prior write requires a fresh read and a new proposal."
        )
    if entity.get("id") != proposal.get("qid"):
        raise ValueError("The repair QID does not match the entity.")
    if entity.get("lastrevid") != proposal.get("revision"):
        raise ValueError("The entity revision changed; prepare a new proposal.")
    if entity_digest(entity) != proposal.get("entity_sha256"):
        raise ValueError("The entity digest changed; prepare a new proposal.")
    claims = entity.get("claims")
    if not isinstance(claims, Mapping):
        raise ValueError("Live claims must be an object.")
    by_id: dict[str, dict[str, object]] = {}
    for rows in claims.values():
        for claim in _objects(rows, "Claims"):
            claim_id = claim.get("id")
            if not isinstance(claim_id, str) or claim_id in by_id:
                raise ValueError("The live entity has absent or duplicate claim IDs.")
            by_id[claim_id] = claim
    seen: set[str] = set()
    for change in _objects(proposal.get("changes"), "Repair changes"):
        claim_id = change.get("claim_id")
        if not isinstance(claim_id, str) or claim_id in seen:
            raise ValueError("The proposal has absent or duplicate claim IDs.")
        seen.add(claim_id)
        before = by_id.get(claim_id)
        if before is None or before != change.get("before"):
            raise ValueError("The repair claim changed.")
        kind = change.get("kind")
        if kind == "remove_duplicate_colophon":
            retained = by_id.get(str(change.get("retained_claim_id")))
            if retained is None or not is_source_exact_colophon_duplicate(
                before, retained, str(change.get("source_text") or "")
            ):
                raise ValueError("The duplicate colophon lacks source and scope evidence.")
            if change.get("after") is not None:
                raise ValueError("A duplicate removal must have no after claim.")
            effective = next(
                (
                    row.get("after")
                    for row in _objects(proposal.get("changes"), "Repair changes")
                    if row.get("claim_id") == retained["id"]
                ),
                retained,
            )
            if not isinstance(effective, Mapping):
                raise ValueError("The retained colophon cannot be removed.")
            effective_ids = {
                _reference_identity(ref)
                for ref in _objects(effective.get("references", []), "References")
            }
            if any(
                _reference_identity(ref) not in effective_ids
                for ref in _objects(before.get("references", []), "References")
            ):
                raise ValueError("The duplicate removal loses a distinct source.")
        else:
            expected = deepcopy(before)
            references = _objects(before.get("references", []), "References")
            if kind == "preserve_colophon_references":
                for removed in _objects(proposal.get("changes"), "Repair changes"):
                    if (
                        removed.get("kind") == "remove_duplicate_colophon"
                        and removed.get("retained_claim_id") == claim_id
                    ):
                        old = by_id.get(str(removed.get("claim_id")))
                        if old is None or not is_source_exact_colophon_duplicate(
                            old, before, str(removed.get("source_text") or "")
                        ):
                            raise ValueError("The colophon reference source cannot be verified.")
                        references.extend(_objects(old.get("references", []), "References"))
            elif kind != "deduplicate_exact_references":
                raise ValueError("The repair operation is unsupported.")
            expected["references"] = _distinct_references(references)
            if expected != change.get("after"):
                raise ValueError("The proposal changes unsupported claim fields.")
