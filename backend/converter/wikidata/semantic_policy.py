"""Versioned deterministic policy for Wikidata semantic checks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class SemanticQualityPolicy:
    """Policy inputs that vary by publication profile."""

    version: str
    strong_external_id_properties: frozenset[str]


MHM_SEMANTIC_POLICY_V1 = SemanticQualityPolicy(
    version="mhm-v1",
    strong_external_id_properties=frozenset({
        "P214", "P8189", "P244", "P227", "P213", "P268",
    }),
)

SEMANTIC_POLICY_REGISTRY: Mapping[str, SemanticQualityPolicy] = MappingProxyType({
    MHM_SEMANTIC_POLICY_V1.version: MHM_SEMANTIC_POLICY_V1,
})

DEFAULT_SEMANTIC_POLICY = MHM_SEMANTIC_POLICY_V1
