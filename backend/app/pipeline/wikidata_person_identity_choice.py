"""Bind a reference-only public person choice to exact canonical source evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import urllib.parse
import urllib.request
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

SOURCE = "wikidata_source_identity_choice"
_API = "https://www.wikidata.org/w/api.php"
_QID = re.compile(r"Q[1-9][0-9]*")
_AUTHORITY = re.compile(r"987[0-9]{6,17}")
_CONTROL = re.compile(r"990[0-9]{15}")


@dataclass(frozen=True)
class PublicPersonTarget:
    qid: str
    authority_id: str


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _value(snak: object) -> object:
    if not isinstance(snak, Mapping) or snak.get("snaktype") != "value":
        raise ValueError("A scoped identity needs value snaks.")
    data = snak.get("datavalue")
    if not isinstance(data, Mapping):
        raise ValueError("A scoped identity has no datavalue.")
    return data.get("value")


def identity_source_hash(control_number: str, record: Mapping[str, Any]) -> str:
    return digest(
        {
            "control_number": control_number,
            "identity_fields": {
                key: value
                for key, value in record.items()
                if key.startswith(("100$", "600$", "700$"))
            },
        }
    )


def _name_forms(value: str) -> set[str]:
    name = " ".join(value.replace('""', '"').strip('" ').rstrip(" ,.;:/").split())
    forms = {name}
    if name.count(",") == 1:
        family, given = name.split(",")
        forms.add(" ".join((given.strip(), family.strip())))
    return forms


def _attested(raw: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    names: set[str] = set()
    labels = raw.get("labels") or {}
    label_values = [labels["he"]] if labels.get("he") else list(labels.values())
    for label in label_values:
        text = label.get("value") if isinstance(label, Mapping) else label
        if isinstance(text, str):
            names.update(_name_forms(text))
    names.discard("")
    for tag in ("100$a", "600$a", "700$a"):
        value = record.get(tag)
        values = value if isinstance(value, list) else [value]
        for part in values:
            if isinstance(part, str):
                for name in part.split("|"):
                    if names & _name_forms(name):
                        return True
    return False


def _active(claim: Mapping[str, Any]) -> bool:
    return claim.get("rank", "normal") in {"normal", "preferred"}


def reference_choice(item: object) -> dict[str, Any] | None:
    evidence = (
        item.get("authority_evidence", [])
        if isinstance(item, Mapping)
        else getattr(item, "authority_evidence", [])
    )
    matches = [entry for entry in evidence if entry.get("source") == SOURCE]
    if not matches:
        return None
    if (
        len(matches) != 1
        or matches[0].get("accepted") is not True
        or matches[0].get("reference_only") is not True
    ):
        raise ValueError("The person choice has conflicting evidence.")
    result = matches[0]
    qid = (
        item.get("existing_qid")
        if isinstance(item, Mapping)
        else getattr(item, "existing_qid", None)
    )
    scope = item.get("records") if isinstance(item, Mapping) else getattr(item, "records", None)
    if (
        qid != result.get("identifier")
        or sorted(scope or []) != result.get("control_numbers")
        or not result.get("proof_hash")
        or not _QID.fullmatch(str(qid or ""))
    ):
        raise ValueError("The person choice no longer matches the projected identity.")
    return result


def authority_id(item: Mapping[str, Any]) -> str:
    ids = {
        str(s.get("value"))
        for s in item.get("statements", [])
        if s.get("property_id") == "P8189" and s.get("rank", "normal") != "deprecated"
    }
    if len(ids) != 1 or not _AUTHORITY.fullmatch(next(iter(ids), "")):
        raise ValueError("The source person needs one exact NLI authority identifier.")
    return next(iter(ids))


def make_choice(
    item,
    source,
    records,
    public_proof,
    *,
    control_numbers,
    expected_qid,
    expected_fingerprint,
    approved,
):
    if approved is not True or item.get("entity_type") != "person":
        raise ValueError("The current person needs curator approval.")
    if not expected_fingerprint or source.get("source_fingerprint") != expected_fingerprint:
        raise ValueError("The canonical source fingerprint changed.")
    if item.get("existing_qid") != expected_qid:
        raise ValueError("The current public target changed.")
    target = public_proof.get("qid")
    authority = authority_id(item)
    if (
        not _QID.fullmatch(str(target or ""))
        or public_proof.get("authority_id") != authority
        or public_proof.get("unique") is not True
        or type(public_proof.get("revision")) is not int
        or public_proof["revision"] <= 0
        or not re.fullmatch(r"[a-f0-9]{64}", str(public_proof.get("entity_sha256") or ""))
    ):
        raise ValueError("The public identifier proof is incomplete or ambiguous.")
    scope = sorted(control_numbers)
    if (
        not scope
        or len(set(scope)) != len(scope)
        or any(not _CONTROL.fullmatch(cn) for cn in scope)
        or not set(scope).issubset(item.get("records") or [])
    ):
        raise ValueError("The source scope is absent, duplicated, or outside this person.")
    hashes = {}
    for cn in scope:
        record = records.get(cn)
        if record is None or not _attested(item, record):
            raise ValueError("The source record does not attest the exact person name.")
        hashes[cn] = identity_source_hash(cn, record)
    choice = {
        "version": 1,
        "local_id": item["local_id"],
        "target_qid": target,
        "previous_qid": expected_qid,
        "projection_existing_qid": item.get("projection_existing_qid", expected_qid),
        "authority_id": authority,
        "source_fingerprint": expected_fingerprint,
        "source_uri": source["source_uri"],
        "labels": deepcopy(item.get("labels") or {}),
        "aliases": deepcopy(item.get("aliases") or {}),
        "original_records": sorted(item.get("records") or []),
        "control_numbers": scope,
        "source_identity_hashes": hashes,
        "public_proof": deepcopy(public_proof),
    }
    choice["proof_hash"] = digest(choice)
    return choice


def apply_choice(item, choice, *, source, records):
    from converter.wikidata.property_mapping import nli_reference

    proof = {key: value for key, value in choice.items() if key != "proof_hash"}
    if choice.get("version") != 1 or digest(proof) != choice.get("proof_hash"):
        raise ValueError("The person choice proof is invalid.")
    if (
        source.get("source_fingerprint") != choice.get("source_fingerprint")
        or source.get("source_uri") != choice.get("source_uri")
        or item.get("local_id") != choice.get("local_id")
        or item.get("entity_type") != "person"
        or item.get("labels") != choice.get("labels")
        or item.get("aliases", {}) != choice.get("aliases")
    ):
        raise ValueError("The person choice needs review after a source identity change.")
    marker = reference_choice(item)
    if marker:
        if marker["proof_hash"] != choice["proof_hash"]:
            raise ValueError("The person choice conflicts with a previous source choice.")
    elif (
        sorted(item.get("records") or []) != choice["original_records"]
        or authority_id(item) != choice["authority_id"]
        or item.get("existing_qid")
        not in {choice["previous_qid"], choice["projection_existing_qid"]}
    ):
        raise ValueError("The person choice no longer matches its original identity.")
    for cn, expected_hash in choice["source_identity_hashes"].items():
        if cn not in records or identity_source_hash(cn, records[cn]) != expected_hash:
            raise ValueError("The source person fields changed after review.")
    out = deepcopy(item)
    out["existing_qid"] = choice["target_qid"]
    out["records"] = list(choice["control_numbers"])
    out["descriptions"] = {"en": "person attested in a manuscript catalogue"}
    blocks = [list(nli_reference(cn)) for cn in out["records"]]
    flat = []
    for block in blocks:
        for snak in block:
            if snak not in flat:
                flat.append(deepcopy(snak))
    out["statements"] = [
        {
            "property_id": pid,
            "value": value,
            "value_type": datatype,
            "references": deepcopy(flat),
            "reference_blocks": deepcopy(blocks),
        }
        for pid, value, datatype in (
            ("P31", "Q5", "item"),
            ("P8189", choice["authority_id"], "external-id"),
        )
    ]
    out["authority_evidence"] = [
        entry
        for entry in out.get("authority_evidence", [])
        if entry.get("source") != SOURCE and entry.get("accepted") is not True
    ]
    out["authority_evidence"].append(
        {
            "source": SOURCE,
            "kind": "wikidata",
            "accepted": True,
            "identifier": choice["target_qid"],
            "reference_only": True,
            "control_numbers": list(out["records"]),
            "proof_hash": choice["proof_hash"],
            "nli_authority_id": choice["authority_id"],
        }
    )
    return out


def validate_incoming_scope(items):
    scopes = {
        item["local_id"]: set(marker["control_numbers"])
        for item in items
        if (marker := reference_choice(item)) is not None
    }
    for item in items:
        for statement in item.get("statements", []):
            target = str(statement.get("value") or "").removeprefix("__LOCAL:")
            if target not in scopes:
                continue
            own = set(item.get("records") or [])
            if not own or not own.issubset(scopes[target]):
                raise ValueError("A relation exceeds the reviewed person scope.")


def _get(params: Mapping[str, str]) -> dict[str, Any]:
    url = _API + "?" + urllib.parse.urlencode({"format": "json", **params})
    request = urllib.request.Request(  # noqa: S310 — Fixed official HTTPS endpoint.
        url,
        headers={
            "User-Agent": "MHM-Pipeline-Scoped-Identity/1.0 (+https://mhm-pipeline.org/)",
        },
    )
    with urllib.request.urlopen(request, timeout=40) as response:  # noqa: S310 — Fixed HTTPS API.
        data = json.load(response)
    if not isinstance(data, dict) or data.get("error"):
        raise ValueError("The public identity lookup did not return a conclusive result.")
    return data


def _public_proofs(choices: list[PublicPersonTarget]) -> dict[str, dict[str, Any]]:
    qids = sorted({choice.qid for choice in choices})
    entities: dict[str, Any] = {}
    for start in range(0, len(qids), 50):
        data = _get(
            {
                "action": "wbgetentities",
                "ids": "|".join(qids[start : start + 50]),
                "props": "claims|info|labels",
            }
        )
        entities.update(data.get("entities") or {})
    proofs = {}
    for choice in choices:
        entity = entities.get(choice.qid)
        if not isinstance(entity, Mapping) or entity.get("id") != choice.qid:
            raise ValueError("The chosen public person does not exist.")
        claims = entity.get("claims") or {}
        ids = {_value(c["mainsnak"]) for c in claims.get("P8189", []) if _active(c)}
        classes = [_value(c["mainsnak"]) for c in claims.get("P31", []) if _active(c)]
        if ids != {choice.authority_id} or not any(
            isinstance(value, Mapping) and value.get("id") == "Q5" for value in classes
        ):
            raise ValueError("The chosen public person has conflicting identity evidence.")
        result = _get(
            {
                "action": "query",
                "list": "search",
                "srnamespace": "0",
                "srlimit": "2",
                "srsearch": "haswbstatement:P8189=" + choice.authority_id,
            }
        )
        hits = [hit.get("title") for hit in (result.get("query") or {}).get("search", [])]
        if (
            hits != [choice.qid]
            or result.get("continue")
            or (result.get("query") or {}).get("searchinfo", {}).get("totalhits", 1) != 1
        ):
            raise ValueError("The public authority identifier is absent or ambiguous.")
        revision = entity.get("lastrevid")
        if type(revision) is not int or revision <= 0:
            raise ValueError("The public identity proof has no current revision.")
        proofs[choice.qid] = {
            "qid": choice.qid,
            "authority_id": choice.authority_id,
            "revision": revision,
            "entity_sha256": digest(entity),
            "unique": True,
        }
    return proofs


async def load_public_proofs(choices: list[PublicPersonTarget]) -> dict[str, dict[str, Any]]:
    if not choices:
        return {}
    return await asyncio.to_thread(_public_proofs, choices)
