"""Preserve the source metadata that the HMO read-back contract supports."""

import re
from collections.abc import Callable, Mapping
from typing import Any

from wikibaseintegrator.models import Reference
from wikibaseintegrator.wbi_enums import WikibaseRank

from converter.wikibase.resolved_models import ResolvedClaim

_SUPPORTED_METADATA_TYPES = frozenset({"string", "external-id", "url", "time"})


def _metadata_claim(snak: object) -> ResolvedClaim:
    if not isinstance(snak, Mapping) or set(snak) != {"property", "type", "value"}:
        raise ValueError("HMO claim metadata needs a property, type, and value.")
    pid, datatype = snak["property"], snak["type"]
    if (
        not isinstance(pid, str)
        or re.fullmatch(r"P[1-9][0-9]*", pid) is None
        or not isinstance(datatype, str)
    ):
        raise ValueError("HMO claim metadata has an invalid property or type.")
    if datatype not in _SUPPORTED_METADATA_TYPES:
        raise ValueError(f"Unsupported HMO claim metadata type: {datatype}.")
    return ResolvedClaim(pid, datatype, snak["value"])


def apply_claim_metadata(
    claim: ResolvedClaim,
    built: Any,
    build_value: Callable[[ResolvedClaim], Any],
) -> Any:
    built.rank = WikibaseRank(claim.rank)
    if not isinstance(claim.qualifiers, list) or not isinstance(claim.reference_blocks, list):
        raise ValueError("HMO claim metadata must use lists.")
    for snak in claim.qualifiers:
        built.qualifiers.add(build_value(_metadata_claim(snak)))
    for block in claim.reference_blocks:
        if not isinstance(block, list) or not block:
            raise ValueError("HMO claim metadata has an empty or invalid reference block.")
        reference = Reference()
        for snak in block:
            reference.add(build_value(_metadata_claim(snak)))
        built.references.add(reference)
    return built
