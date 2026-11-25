"""Apply exact evidence for reviewed MARC 730 occurrences."""

import json
from collections import Counter
from functools import lru_cache
from pathlib import Path

from app.pipeline import marc_occurrences as marc
from app.pipeline.wikidata_gila_execution import semantic_entity
from app.pipeline.wikidata_gila_facts import _live_claims, _reference_status, _snak_value, _text
from app.pipeline.wikidata_gila_sources import source_case
from app.pipeline.wikidata_source_choices import digest

_DATA = Path(__file__).resolve().parents[1] / "data" / "gila_reviewed_uniform_titles.json"


@lru_cache(maxsize=1)
def uniform_title_reviews():
    return json.loads(_DATA.read_text(encoding="utf-8"))


def uniform_title_targets():
    return {
        proof["value"] for reviews in uniform_title_reviews().values()
        for review in reviews for proof in review["works"]
    }


def _work_evidence(cn, manuscript, entity, proof):
    if not entity or entity["id"] != proof["value"] or not entity.get("lastrevid"):
        return {}
    target_titles = {_text(c["value"]) for c in _live_claims(entity, "P1476")}
    target_titles.update(_text(term.get("value")) for term in entity.get("labels", {}).values())
    if _text(proof["target_title"]) not in target_titles:
        return {}
    title = proof["title"]
    folio = proof.get("folio")
    qualifiers = {"P1932": title, **({"P958": folio} if folio else {})}
    accepted_titles = {_text(title), *map(_text, proof.get("equivalent_titles", ()))}
    links = _live_claims(manuscript, "P1574")
    related = [row for row in links if row["value"] == entity["id"] or any(
        (q["property"] == "P1932" and _text(q["value"]) in accepted_titles)
        or (folio and q["property"] == "P958" and _text(q["value"]) == _text(folio))
        for q in row["qualifiers"]
    )]
    if not folio:
        separate = {row["claim_id"]: row["value"] for row in links if row not in related}
        if separate != proof.get("separate_claims", {}):
            return {}
    retirement = proof.get("retire")
    if related and related[0]["value"] != entity["id"]:
        if not retirement or len(related) != 1 or related[0]["claim_id"] != retirement["id"]:
            return {}
        old = next(row for row in manuscript["claims"]["P1574"] if row["id"] == retirement["id"])
        if digest(semantic_entity(old)) != retirement["sha256"]:
            return {}
    elif len(related) > 1:
        return {}
    else:
        retirement = None
    if related and not retirement:
        claim = related[0]
        if _reference_status(related, cn, live=True) != "source_bound" or not claim["claim_id"]:
            return {}
        current_folios = [q["value"] for q in claim["qualifiers"] if q["property"] == "P958"]
        current_titles = [q["value"] for q in claim["qualifiers"] if q["property"] == "P1932"]
        if current_titles and (len(current_titles) != 1 or _text(current_titles[0]) not in accepted_titles):
            return {}
        if current_folios == ([folio] if folio else []):
            return {
                "verified": True, "property": "P1574", "expected_qualifiers": {
                    **({"P1932": current_titles[0]} if current_titles else {}),
                    **({"P958": folio} if folio else {}),
                },
                "verified_matches": [{"statement_id": claim["claim_id"], "value": entity["id"]}],
                "verified_target_qid": manuscript["id"], "verified_target_revision": manuscript["lastrevid"],
            }
        extension = proof.get("extend")
        if current_folios or not folio or not current_titles or not extension or claim["claim_id"] != extension["id"]:
            return {}
        old = next(row for row in manuscript["claims"]["P1574"] if row["id"] == extension["id"])
        if digest(semantic_entity(old)) != extension["sha256"]:
            return {}
        return {"source_repair": {
            "qid": manuscript["id"], "revision": manuscript["lastrevid"], "property": "P1574",
            "value": entity["id"], "qualifiers": {"P958": folio}, "type": "item", "language": None,
            "dependencies": {entity["id"]: entity["lastrevid"]}, "proof_urls": proof["urls"],
            "extend_folio_claim": claim["claim_id"],
        }}
    if any(row.get("rank") == "deprecated" and _snak_value(row["mainsnak"]) == entity["id"]
           for row in manuscript["claims"].get("P1574", [])):
        return {}
    if proof.get("retire") and not retirement:
        return {}
    evidence = source_case(
        cn, proof["covers"][0], title, "reviewed_uniform_title", entity=manuscript, pid="P1574",
        value=entity["id"], qualifiers=qualifiers, kind="item", dependencies=(entity,), proof_urls=proof["urls"],
    )
    if retirement and (repair := evidence.get("source_repair")):
        repair.update(retire_claim=retirement["id"], retire_digest=retirement["sha256"])
    return evidence


def reviewed_uniform_title(cn, record, matched_fields, manuscript, entities):
    reviews = uniform_title_reviews().get(cn, ())
    if not reviews or not manuscript or {c["value"] for c in _live_claims(manuscript, "P3959")} != {cn}:
        return {}
    fields = {marc.source_key(row): row for row in marc.occurrences(record)}
    matched_keys = {marc.source_key(row) for row in matched_fields}
    for review in reviews:
        if manuscript["id"] != review["qid"] or matched_keys != set(review["uniform_fields"]):
            continue
        if any(key not in fields or digest(fields[key]) != value
               for key, value in review["fields"].items()):
            return {}
        proofs = review["works"]
        covered = Counter(key for proof in proofs for key in proof["covers"])
        if (
            not matched_keys <= review["fields"].keys()
            or not proofs
            or any(not proof["covers"] for proof in proofs)
            or not covered <= Counter(matched_keys)
            or len({(proof["value"], proof.get("folio")) for proof in proofs}) != len(proofs)
        ):
            return {}
        checks = [_work_evidence(cn, manuscript, entities.get(proof["value"]), proof) for proof in proofs]
        pending = [check["source_repair"] for check in checks if check.get("source_repair")]
        if all(check.get("verified") for check in checks) and covered.keys() == matched_keys:
            return {**checks[0], "reason": "verified_uniform_title",
                    "verified_matches": [match for check in checks for match in check["verified_matches"]]}
        if pending:
            first, *others = pending
            if others and any(part.get("retire_claim") for part in pending):
                return {}
            return {
                "reason": "reviewed_uniform_title_ready",
                "source_repair": {
                    **first, "additional_claims": others,
                    "dependencies": {qid: rev for part in pending for qid, rev in part["dependencies"].items()},
                },
            }
        return {}
    return {}
