"""Exact corrections to the reviewed shelfmarks and folio links."""

from functools import lru_cache
import json
from pathlib import Path

from app.pipeline import marc_occurrences as marc
from app.pipeline.wikidata_gila_execution import semantic_entity
from app.pipeline.wikidata_source_choices import digest

_DATA = Path(__file__).resolve().parents[1] / "data" / "gila_reviewed_holdings_and_contents.json"


@lru_cache(maxsize=1)
def reviewed_corrections():
    return json.loads(_DATA.read_text(encoding="utf-8"))


def reviewed_work_targets():
    return {row["value"] for row in reviewed_corrections().values() if row["property"] == "P1574"}


def reviewed_case(cn, record, entity, entities, *, holding=None, fact_id=None):
    from app.pipeline.wikidata_gila_facts import _live_claims, _reference_status
    from app.pipeline.wikidata_gila_sources import source_case
    from converter.wikidata.property_mapping import nli_catalog_url

    proof = reviewed_corrections().get(cn)
    if (
        not proof or not record or not entity or entity.get("id") != proof["qid"]
        or {claim["value"] for claim in _live_claims(entity, "P3959")} != {cn}
        or proof.get("fact_id") != fact_id
    ):
        return {}
    fields = {marc.source_key(row): row for row in marc.occurrences(record)}
    field = fields.get(proof["source_field"])
    if not field or digest(field) != proof["source_sha256"]:
        return {}
    pid = proof["property"]
    qualifiers = proof["qualifiers"]
    active = _live_claims(entity, pid)
    if pid == "P217":
        if not holding or holding[0] != field or holding[1] != proof["value"]:
            return {}
        previous_field = fields.get(proof["previous_field"])
        if digest(previous_field) != proof["previous_field_sha256"]:
            return {}
        dependencies = ()
        source_text = holding[1]
    else:
        target = entities.get(proof["value"])
        if (not target or target.get("id") != proof["value"]
            or proof["target_title"] not in {claim["value"] for claim in _live_claims(target, "P1476")}):
            return {}
        active = [
            claim for claim in active
            if any(q["property"] == "P958" and q["value"] == qualifiers["P958"] for q in claim["qualifiers"])
        ]
        dependencies = (target,)
        source_text = " ".join(marc.values(field, "a"))
    if len(active) != 1 or len(active[0]["qualifiers"]) != len(qualifiers):
        return {}
    claim = active[0]
    if {q["property"]: q["value"] for q in claim["qualifiers"]} != qualifiers:
        return {}
    if _reference_status([claim], cn, live=True) != "source_bound":
        return {}
    if claim["value"] != proof["value"]:
        if claim["claim_id"] != proof["previous_claim"]:
            return {}
        old = next(row for row in entity["claims"][pid] if row.get("id") == claim["claim_id"])
        if digest(semantic_entity(old)) != proof["previous_claim_sha256"]:
            return {}
        if any(
            row.get("mainsnak", {}).get("datavalue", {}).get("value") == proof["value"]
            for row in entity["claims"][pid]
        ):
            return {}
    evidence = source_case(
        cn, proof["source_field"], source_text, "reviewed_holding_or_work",
        entity=entity, pid=pid, value=proof["value"], qualifiers=qualifiers, kind=proof["type"],
        proof_urls=(nli_catalog_url(cn),), dependencies=dependencies,
    )
    if repair := evidence.get("source_repair"):
        if claim["value"] == proof["value"]:
            return {}
        repair["retire_claim"] = proof["previous_claim"]
        repair["retire_digest"] = proof["previous_claim_sha256"]
        if holding:
            repair["en_label"] = holding[2]
            if entity.get("descriptions", {}).get("en", {}).get("value") == proof["previous_description"]:
                repair["en_description"] = proof["description"]
    return evidence
