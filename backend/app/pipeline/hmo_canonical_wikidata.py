"""Safe preparation of Wikidata candidates from canonical HMO entities."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
import hashlib
import json
import re

from app.pipeline.hmo_canonical import CanonicalHmoEntity, assert_canonical_entities
from converter.wikidata.item_models import WikidataItem, WikidataStatement


def wikidata_candidates_from_hmo(
    entities: Iterable[CanonicalHmoEntity],
) -> list[dict[str, Any]]:
    """Return projection records grounded exclusively in HMO state.

    This is intentionally a neutral candidate format.  The Wikidata builder
    may add only mappings explicitly declared by the projection layer; raw
    AuthorityMatch rows are not accepted as inputs.
    """
    materialized = list(entities)
    assert_canonical_entities(materialized)
    return [
        {
            "local_id": entity.local_id,
            "source_uri": entity.source_uri,
            "hmo_wikibase_id": entity.wikibase_id,
            "labels": dict(entity.labels),
            "descriptions": dict(entity.descriptions),
            "aliases": dict(entity.aliases),
            "claims": list(entity.claims),
            "authority_evidence": [
                evidence for evidence in entity.authority_evidence
                if evidence.get("accepted") is True
            ],
            "projection_source": "hmo_wikibase",
            "source_fingerprint": entity.source_fingerprint,
            "entity_type": entity.entity_type,
        }
        for entity in materialized
    ]


def canonical_wikidata_fingerprint(entities: Iterable[CanonicalHmoEntity]) -> str:
    candidates = wikidata_candidates_from_hmo(entities)
    payload = json.dumps(candidates, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(("hmo-wikidata-v1:" + payload).encode()).hexdigest()


_PROPERTY_ID = re.compile(r"^P[1-9][0-9]*$")
_QID = re.compile(r"^Q[1-9][0-9]*$")

def native_wikidata_claims(entity: CanonicalHmoEntity) -> list[dict[str, str]]:
    native: list[dict[str, str]] = []
    for claim in entity.claims:
        property_id = str(claim.get("wikidata_property") or "").strip()
        if not _PROPERTY_ID.fullmatch(property_id):
            continue
        raw = str(claim.get("wikidata_value") or claim.get("value") or claim.get("target_qid") or "").strip()
        if not raw or (claim.get("value_type") == "wikibase-item" and not _QID.fullmatch(raw)):
            continue
        native.append({"property": property_id, "value": raw})
    return native


def native_items_from_hmo(entities: Iterable[CanonicalHmoEntity]) -> list[WikidataItem]:
    """Adapt live HMO snapshots to the guarded Wikidata upload model."""
    materialized = list(entities)
    assert_canonical_entities(materialized)
    items: list[WikidataItem] = []
    for entity in materialized:
        accepted_qids = {
            str(evidence.get("value") or evidence.get("wikidata_qid") or "").strip()
            for evidence in entity.authority_evidence
            if evidence.get("accepted") is True
            and str(evidence.get("kind") or "").lower() == "wikidata"
        }
        accepted_qids.discard("")
        existing_qid = next(iter(accepted_qids)) if len(accepted_qids) == 1 else None
        statements = [
            WikidataStatement(
                property_id=claim["property"],
                value=claim["value"],
                value_type="wikibase-item" if _QID.fullmatch(claim["value"]) else "string",
            )
            for claim in native_wikidata_claims(entity)
        ]
        items.append(WikidataItem(
            labels=dict(entity.labels),
            descriptions=dict(entity.descriptions),
            aliases=dict(entity.aliases),
            statements=statements,
            existing_qid=existing_qid,
            entity_type=entity.entity_type,
            local_id=entity.local_id,
            authority_evidence=[
                evidence for evidence in entity.authority_evidence
                if evidence.get("accepted") is True
            ],
        ))
    return items


def quickstatements_from_canonical(entities: Iterable[CanonicalHmoEntity]) -> str:
    lines: list[str] = []
    for entity in entities:
        lines.append("CREATE")
        for lang, label in sorted(entity.labels.items()):
            safe = str(label).replace(chr(34), chr(92) + chr(34))
            lines.append("LAST\tL" + lang + "\t\"" + safe + "\"")
        for claim in native_wikidata_claims(entity):
            value = claim["value"]
            encoded = value if _QID.fullmatch(value) else chr(34) + value.replace(chr(34), chr(92) + chr(34)) + chr(34)
            lines.append("LAST\t" + claim["property"] + "\t" + encoded)
    return "\n".join(lines) + ("\n" if lines else "")
