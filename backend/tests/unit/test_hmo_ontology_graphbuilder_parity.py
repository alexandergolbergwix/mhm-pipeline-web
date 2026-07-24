"""GraphBuilder hm: terms and Wikidata mapper stay 1:1 with the ontology (W-102)."""

from __future__ import annotations

import re
from pathlib import Path

from converter.wikidata.hmo_wikidata_pq_mapper import HMO_LOCAL_NAME_TO_WIKIDATA_PID

_BACKEND = Path(__file__).resolve().parents[2]
_TTL = _BACKEND / "ontology" / "hebrew-manuscripts.ttl"
_GRAPH_BUILDER = _BACKEND / "converter" / "rdf" / "graph_builder.py"


def _declared_hm_terms() -> set[str]:
    text = _TTL.read_text(encoding="utf-8")
    return set(re.findall(r"^hm:([A-Za-z0-9_]+)\b", text, flags=re.M))


def _equivalent_property_map() -> dict[str, str]:
    text = _TTL.read_text(encoding="utf-8")
    return {
        local: pid
        for local, pid in re.findall(
            r"hm:([A-Za-z0-9_]+)\s+owl:equivalentProperty\s+"
            r"<http://www\.wikidata\.org/prop/direct/(P\d+)>",
            text,
        )
    }


def test_graph_builder_hm_symbols_are_declared_in_ontology() -> None:
    used = set(re.findall(r"\bHM\.([A-Za-z0-9_]+)\b", _GRAPH_BUILDER.read_text(encoding="utf-8")))
    declared = _declared_hm_terms()
    missing = sorted(used - declared)
    assert missing == [], f"GraphBuilder emits undeclared hm: terms: {missing}"


def test_enrichment_identifier_properties_are_declared() -> None:
    declared = _declared_hm_terms()
    for name in ("mazal_id", "kima_id", "authority_id", "viaf_id", "geonames_id", "wikidata_id"):
        assert name in declared


def test_mapper_keys_present_in_ttl_have_matching_equivalent_property() -> None:
    """Every mapper local-name that is a declared hm: term must agree with TTL equivalents."""
    declared = _declared_hm_terms()
    equivalents = _equivalent_property_map()
    mismatches: list[str] = []
    for local, pid in HMO_LOCAL_NAME_TO_WIKIDATA_PID.items():
        if local not in declared:
            continue
        if local == "kima_id":
            continue
        ttl_pid = equivalents.get(local)
        if ttl_pid is None:
            mismatches.append(f"{local}: mapper={pid} but no owl:equivalentProperty in TTL")
        elif ttl_pid != pid:
            mismatches.append(f"{local}: mapper={pid} TTL={ttl_pid}")
    assert mismatches == []
