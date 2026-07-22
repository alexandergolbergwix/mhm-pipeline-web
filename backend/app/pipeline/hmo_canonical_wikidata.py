"""Safe preparation of Wikidata candidates from canonical HMO entities."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
import hashlib
import json
import re

from app.pipeline.hmo_canonical import CanonicalHmoEntity, assert_canonical_entities


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
