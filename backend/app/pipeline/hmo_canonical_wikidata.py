"""Safe preparation of Wikidata candidates from canonical HMO entities."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

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
