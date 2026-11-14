"""Versioned source-specific choices for holdings, work occurrences, and citations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from urllib.parse import urlsplit

import requests

from app.pipeline import marc_occurrences
from app.pipeline.marc_ingest import prepare_record_for_pipeline

KINDS = frozenset({"holding", "work_occurrence", "catalog_reference", "passage"})
SOURCE = "wikidata_source_review_choice"


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def source_hash(record: Mapping[str, object]) -> str:
    normalized = deepcopy(dict(record))
    marc_occurrences.attach(normalized)
    if "marc_occurrences" in normalized:
        return digest({"marc_occurrences": normalized["marc_occurrences"]})
    raw = {key: value for key, value in normalized.items() if re.match(r"^\d{3}(?:\$|$)", key)}
    raw["001"] = str(record.get("001") or record.get("_control_number") or "")
    if not any("$" in key or key in {"marc_occurrences", "fields"} for key in raw):
        raw.update(
            {
                key: record.get(key)
                for key in ("title", "authors", "contributors", "contents", "catalog_references")
            }
        )
    return digest(raw)


def _text(value: object) -> str:
    return " ".join(str(value or "").replace('""', '"').strip('" ').rstrip(" ,.;:/").split())


def candidates(raw: Mapping[str, object]) -> list[dict[str, object]]:
    from converter.wikidata.item_builder import structured_work_attribution

    record = prepare_record_for_pipeline(deepcopy(dict(raw)))
    cn = str(record.get("_control_number") or "")
    result: list[dict[str, object]] = []
    for row in marc_occurrences.occurrences(record, "942"):
        holder, shelfmark = (" ".join(marc_occurrences.values(row, code)) for code in ("a", "z"))
        if (
            not holder
            or not shelfmark
            or "unknown library" in holder.casefold()
            or holder.casefold().startswith(("catalogue ", "catalog "))
        ):
            continue
        result.append(
            {
                "kind": "holding",
                "source_field": "942",
                "source_key": marc_occurrences.source_key(row),
                "title": holder,
                "shelfmark": shelfmark,
                "source_text": " / ".join(
                    f"${sf['code']} {sf['value']}" for sf in row["subfields"]
                ),
                "occurrence": row,
            }
        )
    if not marc_occurrences.occurrences(record, "942"):
        names = marc_occurrences.split_slots(record.get("942$a"))
        shelves = marc_occurrences.split_slots(record.get("942$z"))
        if len(names) == len(shelves):
            for index, (holder, shelfmark) in enumerate(zip(names, shelves, strict=True), 1):
                if (
                    holder
                    and shelfmark
                    and not holder.casefold().startswith(
                        ("catalog ", "catalogue ", "unknown library")
                    )
                ):
                    result.append(
                        {
                            "kind": "holding",
                            "source_field": "942",
                            "source_key": f"flat942:{index}",
                            "title": holder,
                            "shelfmark": shelfmark,
                            "source_text": f"{holder}: {shelfmark}",
                            "association": "flattened_equal_slots",
                        }
                    )
    contents = list(record.get("contents") or [])
    title = str(record.get("title") or "")
    authors = [
        str(a.get("name") or "")
        for a in record.get("authors") or []
        if isinstance(a, Mapping) and str(a.get("role") or "author") == "author"
    ]
    if title:
        contents.append(
            {
                "title": title,
                "source_field": "100/245",
                "author": authors[0] if len(authors) == 1 else "",
            }
        )
    for index, entry in enumerate(contents):
        if not isinstance(entry, dict):
            continue
        if entry.get("approved") is False or entry.get("candidate_kind") in {
            "catalogue_prose",
            "bibliographic_fragment",
            "descriptive_note",
        }:
            continue
        attribution = structured_work_attribution(entry)
        work_title = str(attribution.title or "").strip()
        if not work_title:
            continue
        author = str(
            attribution.author_name or entry.get("author") or entry.get("author_name") or ""
        )
        result.append(
            {
                "kind": "work_occurrence",
                "source_field": str(entry.get("source_field") or "505"),
                "source_key": str(entry.get("source_key") or f"content:{index}"),
                "title": work_title,
                "author": author,
                "folio": str(entry.get("folio_range") or ""),
                "scope_text": str(entry.get("scope_text") or ""),
                "scope_alignment_unproven": bool(entry.get("scope_alignment_unproven")),
                "source_text": str(
                    entry.get("raw")
                    or entry.get("source_text")
                    or entry.get("source_title")
                    or work_title
                ),
            }
        )
    for index, reference in enumerate(record.get("catalog_references") or []):
        from converter.wikidata.catalog_references import structured_catalog_reference

        entry = structured_catalog_reference(reference)
        result.append(
            {
                "kind": "catalog_reference",
                "source_field": str(entry.get("source_field") or "581"),
                "source_key": str(entry.get("source_key") or f"citation:{index}"),
                "title": str(entry.get("title") or ""),
                "year": entry.get("year") or entry.get("candidate_year"),
                "author": str(entry.get("author") or ""),
                "identifier": str(entry.get("identifier") or ""),
                "identifier_property": str(entry.get("identifier_property") or ""),
                "pages": str(entry.get("pages") or entry.get("candidate_pages") or ""),
                "source_text": entry["original_citation"],
                "citation": entry,
            }
        )
    from app.pipeline.wikidata_source_coverage import source_passages

    result.extend(
        {
            **passage,
            "kind": "passage",
            "title": "Source passage",
            "source_key": passage["source_key"],
        }
        for passage in source_passages(raw)
    )
    for entry in result:
        entry["control_number"] = cn
        entry["source_hash"] = source_hash(raw)
        entry["candidate_key"] = digest(entry)
    return result


def _claim_values(entity: Mapping[str, object], pid: str) -> list[object]:
    claims = entity.get("claims", {})
    if not isinstance(claims, Mapping):
        return []
    result = []
    for claim in claims.get(pid, []):
        if not isinstance(claim, Mapping) or claim.get("rank") == "deprecated":
            continue
        snak = claim.get("mainsnak", {})
        if isinstance(snak, Mapping) and snak.get("snaktype") == "value":
            data = snak.get("datavalue", {})
            if isinstance(data, Mapping):
                result.append(data.get("value"))
    return result


def make_choice(
    raw: Mapping[str, object],
    candidate_key: str,
    expected_source_hash: str,
    *,
    target: Mapping[str, object] | None = None,
    evidence_uri: str = "",
    decision: str = "select",
    reason: str = "",
) -> dict[str, object]:
    if source_hash(raw) != expected_source_hash:
        raise ValueError("The source record changed. Review its current evidence.")
    candidate = next(
        (row for row in candidates(raw) if row["candidate_key"] == candidate_key), None
    )
    if candidate is None:
        raise ValueError("The source occurrence changed or is absent.")
    parsed = urlsplit(evidence_uri)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("The reviewed choice needs an HTTP evidence URL without credentials.")
    proof: dict[str, object] = {"candidate": candidate, "evidence_uri": evidence_uri, "version": 1}
    if not reason.strip():
        raise ValueError("The source choice needs a reason for the curator decision.")
    proof["reason"] = reason.strip()
    if candidate["kind"] == "passage":
        if decision != "document" or not reason.strip():
            raise ValueError("A source passage needs an unresolved decision and a reason.")
        proof.update(decision="document", reason=reason.strip())
        proof["proof_hash"] = digest(proof)
        return proof
    if decision not in {"select", "reject"}:
        raise ValueError("The source decision must select or reject a match.")
    if decision == "reject":
        if (
            candidate["kind"] == "holding"
            or not re.fullmatch(r"Q[1-9][0-9]*", str((target or {}).get("id") or ""))
            or not reason.strip()
        ):
            raise ValueError("A rejected work or citation match needs a target and a reason.")
        proof.update(decision="reject", rejected_qid=target["id"], reason=reason.strip())
        proof["proof_hash"] = digest(proof)
        return proof
    if candidate["kind"] == "holding":
        selected = deepcopy(dict(raw))
        selected["reviewed_holding_occurrence"] = candidate["source_key"]
        prepared = prepare_record_for_pipeline(selected)
        if not prepared.get("holding_942_name") or not prepared.get("holding_942_shelfmark"):
            raise ValueError("The selected source does not prove a current library and shelfmark.")
    if candidate["kind"] == "work_occurrence" and candidate.get("scope_alignment_unproven"):
        raise ValueError("The work occurrence needs a source-scope review before a target choice.")
    if candidate["kind"] != "holding":
        validate_target(candidate, target)
        proof["target_qid"] = target["id"]
        proof["target_revision"] = target["lastrevid"]
        proof["target_digest"] = digest({k: v for k, v in target.items() if not k.startswith("_")})
    proof["proof_hash"] = digest(proof)
    return proof


def validate_target(candidate, target) -> None:
    if target is None or not re.fullmatch(r"Q[1-9][0-9]*", str(target.get("id") or "")):
        raise ValueError("The reviewed identity needs an existing public target.")
    if (
        target.get("type") != "item"
        or target.get("redirects")
        or type(target.get("lastrevid")) is not int
        or target.get("lastrevid", 0) <= 0
        or "missing" in target
        or "redirect" in target
    ):
        raise ValueError("The target is missing, redirected, or incomplete.")
    labels = target.get("labels", {})
    names = (
        {_text(v.get("value")) for v in labels.values() if isinstance(v, Mapping)}
        if isinstance(labels, Mapping)
        else set()
    )
    names.update(
        _text(v.get("text")) for v in _claim_values(target, "P1476") if isinstance(v, Mapping)
    )
    title = _text(candidate.get("title"))
    citation = _text(candidate.get("source_text"))
    title_matches = bool(title and title in names)
    if candidate["kind"] == "catalog_reference":
        title_matches = title_matches or any(len(n) >= 8 and n in citation for n in names)
    if not title_matches:
        raise ValueError("The public title does not match the source evidence.")
    if candidate["kind"] == "work_occurrence":
        classes = {v.get("id") for v in _claim_values(target, "P31") if isinstance(v, Mapping)}
        if "Q87167" in classes or not classes.intersection({"Q47461344", "Q7725634", "Q571"}):
            raise ValueError("The public target needs a supported work class.")
        author = _text(candidate.get("author"))
        author_names = {_text(v) for v in _claim_values(target, "P2093")}
        author_names.update(_text(v) for v in target.get("_verified_author_names", []))
        source_cns = {
            str(s.get("datavalue", {}).get("value"))
            for claims in target.get("claims", {}).values()
            for claim in claims
            for ref in claim.get("references", [])
            for s in ref.get("snaks", {}).get("P3959", [])
        }
        if author and author not in author_names:
            raise ValueError("The public author does not match the source responsibility.")
        if not author and candidate["control_number"] not in source_cns:
            raise ValueError("A title alone does not establish this work identity.")
    else:
        classes = {v.get("id") for v in _claim_values(target, "P31") if isinstance(v, Mapping)}
        if "Q5" in classes or target.get("id") == "Q118384267":
            raise ValueError("The target cannot serve as the bibliographic publication.")
        identifier = candidate.get("identifier")
        pid = candidate.get("identifier_property")
        identifier_matches = bool(
            identifier
            and pid in {"P212", "P957", "P356"}
            and identifier in _claim_values(target, pid)
        )
        if not candidate.get("year") and not identifier_matches:
            raise ValueError(
                "The citation needs a compatible publication year "
                "or an exact bibliographic identifier."
            )
        if identifier and not identifier_matches:
            raise ValueError("The public bibliographic identifier conflicts with the citation.")
        if candidate.get("year"):
            years = {
                str(v.get("time", ""))[1:5]
                for v in _claim_values(target, "P577")
                if isinstance(v, Mapping)
            }
            if str(candidate["year"]) not in years:
                raise ValueError("The catalogue publication year conflicts with the citation.")
        author = _text(candidate.get("author"))
        if author and author not in {_text(v) for v in target.get("_verified_author_names", [])}:
            raise ValueError("The public bibliographic author conflicts with the citation.")


def load_target_sync(qid: str) -> dict[str, object]:
    if not re.fullmatch(r"Q[1-9][0-9]*", qid):
        raise ValueError("The target QID is invalid.")

    def fetch() -> dict[str, object]:
        def read(ids: list[str]) -> dict[str, object]:
            response = requests.get(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(ids),
                    "props": "info|labels|claims",
                    "format": "json",
                },
                headers={"User-Agent": "MHMPipeline/1.0 (shvedbook@gmail.com)"},
                timeout=9,
                allow_redirects=False,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("error") or data.get("redirects") or "entities" not in data:
                raise ValueError("The public identity lookup failed.")
            return data["entities"]

        target = read([qid]).get(qid)
        if not isinstance(target, dict) or target.get("id") != qid:
            raise ValueError("The public target is missing or redirected.")
        authors = [
            v["id"]
            for v in _claim_values(target, "P50")
            if isinstance(v, Mapping) and isinstance(v.get("id"), str)
        ]
        if len(authors) > 50:
            raise ValueError("The target has too many authors for an interactive identity proof.")
        names = []
        for start in range(0, len(authors), 50):
            for author in read(authors[start : start + 50]).values():
                if isinstance(author, Mapping):
                    names.extend(
                        v["value"]
                        for v in author.get("labels", {}).values()
                        if isinstance(v, Mapping) and isinstance(v.get("value"), str)
                    )
        target["_verified_author_names"] = names
        return target

    try:
        return fetch()
    except requests.RequestException as exc:
        raise ValueError("The current public identity is unavailable. Retry the review.") from exc


async def load_target(qid: str) -> dict[str, object]:
    try:
        return await asyncio.wait_for(asyncio.to_thread(load_target_sync, qid), timeout=22)
    except TimeoutError as exc:
        raise ValueError("The public identity proof exceeded its time budget.") from exc


def validate_choices(
    choices: object, records: Mapping[str, Mapping[str, object]]
) -> list[dict[str, object]]:
    if not isinstance(choices, list):
        raise ValueError("Reviewed source choices must be an array.")
    result = []
    seen = set()
    holdings = set()
    for raw in choices:
        if not isinstance(raw, dict):
            raise ValueError("A source choice needs a validated proof.")
        choice = deepcopy(raw)
        fingerprint = choice.pop("proof_hash", None)
        if fingerprint != digest(choice):
            raise ValueError("The source-choice proof changed.")
        candidate = choice.get("candidate", {})
        if not isinstance(candidate, dict) or candidate.get("kind") not in KINDS:
            raise ValueError("The source choice has no supported candidate.")
        cn = candidate.get("control_number")
        record = records.get(cn)
        if record is None or source_hash(record) != candidate.get("source_hash"):
            raise ValueError("The source choice is stale or outside the current record scope.")
        current = next(
            (
                row
                for row in candidates(record)
                if row["candidate_key"] == candidate.get("candidate_key")
            ),
            None,
        )
        if current != candidate:
            raise ValueError("The reviewed source occurrence changed.")
        identity = (cn, candidate["candidate_key"])
        if candidate["kind"] == "holding":
            if cn in holdings:
                raise ValueError("The source record has conflicting current-library choices.")
            holdings.add(cn)
        if identity in seen:
            raise ValueError("The source occurrence has conflicting choices.")
        seen.add(identity)
        choice["proof_hash"] = fingerprint
        result.append(choice)
    return result


def _work_occurrence_claims(item, candidate):
    return [
        statement
        for statement in item.statements
        if statement.property_id == "P1574"
        and any(
            q.get("property") == "P1932" and _text(q.get("value")) == _text(candidate["title"])
            for q in statement.qualifiers
        )
        and {q.get("value") for q in statement.qualifiers if q.get("property") == "P958"}
        == ({candidate["folio"]} if candidate.get("folio") else set())
    ]


def _claim_record_ids(statement) -> set[str]:
    records = set()
    for block in statement.reference_blocks or [statement.references]:
        for snak in block:
            if (snak.get("property") or snak.get("property_id")) != "P3959":
                continue
            data = snak.get("datavalue", {})
            value = snak.get("value", data.get("value") if isinstance(data, Mapping) else None)
            if value:
                records.add(str(value))
    return records


def _require_exact_record_scope(item, statement, control_number: str) -> None:
    records = _claim_record_ids(statement)
    if (records and records != {control_number}) or (not records and len(item.records) != 1):
        raise ValueError("The claim has another or unproven source-record scope. Review it first.")


def _citation_qualifiers(candidate) -> list[dict[str, object]]:
    return (
        [{"property": "P304", "value": candidate["pages"], "type": "string"}]
        if candidate.get("pages")
        else []
    )


def apply_choices(item, choices: object, records: Mapping[str, Mapping[str, object]]) -> None:
    from converter.wikidata.item_builder import WikidataItemBuilder
    from converter.wikidata.item_models import WikidataStatement, merge_statement_references
    from converter.wikidata.property_mapping import (
        P_DESCRIBED_BY_SOURCE,
        nli_reference,
    )

    scope = {cn: record for cn, record in records.items() if cn in item.records}
    reviewed = validate_choices(choices, scope)
    holdings = {
        (_text(proof["candidate"]["title"]), _text(proof["candidate"]["shelfmark"]))
        for proof in reviewed
        if proof["candidate"]["kind"] == "holding"
    }
    if len(holdings) > 1:
        raise ValueError(
            "The manuscript has conflicting current-library choices across source records."
        )
    work_decisions = {}
    for proof in reviewed:
        candidate = proof["candidate"]
        if candidate["kind"] != "work_occurrence":
            continue
        key = (candidate["control_number"], _text(candidate["title"]), candidate.get("folio"))
        decision = (
            proof.get("decision", "select"),
            proof.get("target_qid") or proof.get("rejected_qid"),
        )
        if key in work_decisions and work_decisions[key] != decision:
            raise ValueError(
                "The work occurrences have conflicting decisions for the same claim scope."
            )
        work_decisions[key] = decision
    for proof in reviewed:
        candidate = proof["candidate"]
        cn = candidate["control_number"]
        if item.entity_type != "manuscript":
            raise ValueError("Source occurrence choices belong to their manuscript.")
        if candidate["kind"] == "passage":
            evidence = {"source": SOURCE, "accepted": False, "proof": proof}
            if evidence not in item.authority_evidence:
                item.authority_evidence.append(evidence)
            continue
        if proof.get("decision") == "reject":
            if candidate["kind"] == "work_occurrence":
                matching = [
                    statement
                    for statement in _work_occurrence_claims(item, candidate)
                    if statement.value == proof["rejected_qid"]
                ]
                if len(matching) > 1:
                    raise ValueError(
                        "The rejected occurrence matches multiple claims. Review their scope."
                    )
                for statement in matching:
                    _require_exact_record_scope(item, statement, cn)
                for statement in matching:
                    statement.value = "Q234460"
                    statement.value_type = "item"
            else:
                matching = [
                    s
                    for s in item.statements
                    if s.property_id == P_DESCRIBED_BY_SOURCE
                    and s.value == proof["rejected_qid"]
                    and s.qualifiers == _citation_qualifiers(candidate)
                    and (not _claim_record_ids(s) or cn in _claim_record_ids(s))
                ]
                for statement in matching:
                    _require_exact_record_scope(item, statement, cn)
                item.statements = [s for s in item.statements if s not in matching]
            evidence = {"source": SOURCE, "accepted": False, "proof": proof}
            if evidence not in item.authority_evidence:
                item.authority_evidence.append(evidence)
            continue
        if candidate["kind"] == "holding":
            raw = deepcopy(dict(scope[cn]))
            raw["reviewed_holding_occurrence"] = candidate["source_key"]
            prepared = prepare_record_for_pipeline(raw)
            fresh = WikidataItemBuilder().build_manuscript_item(prepared)
            replacements = [s for s in fresh.statements if s.property_id in {"P195", "P217"}]
            current = [
                s
                for s in item.statements
                if s.property_id in {"P195", "P217"}
                and not any(q.get("property") in {"P580", "P582", "P585"} for q in s.qualifiers)
            ]
            unchanged = []
            for statement in current:
                equivalent = next(
                    (
                        s
                        for s in replacements
                        if (s.property_id, s.value, s.value_type, s.qualifiers)
                        == (
                            statement.property_id,
                            statement.value,
                            statement.value_type,
                            statement.qualifiers,
                        )
                    ),
                    None,
                )
                if equivalent is None:
                    _require_exact_record_scope(item, statement, cn)
                else:
                    if any(previous is equivalent for _, previous in unchanged):
                        raise ValueError(
                            "The library choice matches multiple claims. Review their source scope."
                        )
                    unchanged.append((statement, equivalent))
            for statement, equivalent in unchanged:
                if cn not in _claim_record_ids(statement):
                    merge_statement_references(statement, equivalent)
                replacements.remove(equivalent)
            for lang in ("en",):
                if fresh.labels.get(lang):
                    item.labels[lang] = fresh.labels[lang]
                if fresh.descriptions.get(lang):
                    item.descriptions[lang] = fresh.descriptions[lang]
            item.statements = [s for s in item.statements if s not in current]
            item.statements.extend(s for s, _ in unchanged)
            item.statements.extend(replacements)
        elif candidate["kind"] == "work_occurrence":
            qualifiers = [{"property": "P1932", "value": candidate["title"], "type": "string"}]
            if candidate.get("folio"):
                qualifiers.append(
                    {"property": "P958", "value": candidate["folio"], "type": "string"}
                )
            matching = _work_occurrence_claims(item, candidate)
            if matching:
                if len(matching) != 1:
                    raise ValueError(
                        "The source occurrence matches multiple claims. Review their scope."
                    )
                for statement in matching:
                    if statement.value != proof["target_qid"]:
                        _require_exact_record_scope(item, statement, cn)
                for statement in matching:
                    statement.value = proof["target_qid"]
                    statement.value_type = "item"
                    blocks = statement.reference_blocks or [statement.references]
                    if not any(
                        snak.get("property") == "P3959" and snak.get("value") == cn
                        for block in blocks
                        for snak in block
                    ):
                        merge_statement_references(
                            statement,
                            WikidataStatement(
                                "P1574", proof["target_qid"], "item", references=nli_reference(cn)
                            ),
                        )
            else:
                item.statements.append(
                    WikidataStatement(
                        property_id="P1574",
                        value=proof["target_qid"],
                        value_type="item",
                        qualifiers=qualifiers,
                        references=nli_reference(cn),
                    )
                )
        else:
            qualifiers = _citation_qualifiers(candidate)
            matching = [
                s
                for s in item.statements
                if s.property_id == P_DESCRIBED_BY_SOURCE
                and s.value == proof["target_qid"]
                and s.qualifiers == qualifiers
            ]
            if len(matching) > 1:
                raise ValueError("The citation matches multiple claims. Review their source scope.")
            if matching:
                if cn not in _claim_record_ids(matching[0]):
                    merge_statement_references(
                        matching[0],
                        WikidataStatement(
                            P_DESCRIBED_BY_SOURCE,
                            proof["target_qid"],
                            "item",
                            references=nli_reference(cn),
                        ),
                    )
            else:
                item.statements.append(
                    WikidataStatement(
                        property_id=P_DESCRIBED_BY_SOURCE,
                        value=proof["target_qid"],
                        value_type="item",
                        qualifiers=qualifiers,
                        references=nli_reference(cn),
                    )
                )
        evidence = {"source": SOURCE, "accepted": True, "proof": proof}
        if evidence not in item.authority_evidence:
            item.authority_evidence.append(evidence)


def revalidate_payload_targets_sync(item: Mapping[str, object]) -> None:
    targets = {}
    for evidence in item.get("authority_evidence", []):
        if not isinstance(evidence, Mapping) or evidence.get("source") != SOURCE:
            continue
        proof = deepcopy(evidence.get("proof") or {})
        fingerprint = proof.pop("proof_hash", None)
        if fingerprint != digest(proof):
            raise ValueError("The source-choice proof changed.")
        candidate = proof["candidate"]
        if proof.get("decision") == "reject" or candidate["kind"] == "passage":
            continue
        if candidate["kind"] != "holding":
            qid = str(proof.get("target_qid") or "")
            if qid not in targets:
                targets[qid] = load_target_sync(qid)
            validate_target(candidate, targets[qid])


async def revalidate_payload_targets(item: Mapping[str, object]) -> None:
    await asyncio.to_thread(revalidate_payload_targets_sync, item)


async def _reprove_choices(
    choices: object, records: Mapping[str, Mapping[str, object]]
) -> list[dict[str, object]]:
    result = []
    for proof in validate_choices(choices, records):
        candidate = proof["candidate"]
        qid = proof.get("target_qid") or proof.get("rejected_qid")
        target = await load_target(qid) if qid else None
        result.append(
            make_choice(
                records[candidate["control_number"]],
                candidate["candidate_key"],
                candidate["source_hash"],
                target=target,
                evidence_uri=proof["evidence_uri"],
                decision=proof.get("decision", "select"),
                reason=proof.get("reason", ""),
            )
        )
    return result


async def reprove_choices(choices, records):
    try:
        return await asyncio.wait_for(_reprove_choices(choices, records), timeout=22)
    except TimeoutError as exc:
        raise ValueError("The source-choice import exceeded its public proof time budget.") from exc


def reviewed_source_choices(item, raw, live_entities=None, approved_author_qids=None):
    """Recheck current source and target evidence for an offline audit."""
    evidence_rows = [
        row
        for row in item.get("authority_evidence") or []
        if isinstance(row, Mapping) and row.get("source") == SOURCE and row.get("accepted") is True
    ]
    if not evidence_rows:
        return []
    cn = str(prepare_record_for_pipeline(dict(raw)).get("_control_number") or "")
    proofs = [
        row.get("proof")
        for row in evidence_rows
        if isinstance(row.get("proof"), Mapping)
        and isinstance(row["proof"].get("candidate"), Mapping)
        and row["proof"]["candidate"].get("control_number") == cn
    ]
    try:
        validated = validate_choices(proofs, {cn: raw})
    except ValueError:
        return []
    result = []
    for checked in validated:
        candidate = checked["candidate"]
        if checked.get("decision") or candidate["kind"] not in {
            "holding",
            "work_occurrence",
            "catalog_reference",
        }:
            continue
        current = False
        target = None
        if candidate["kind"] != "holding":
            public = (live_entities or {}).get(checked["target_qid"])
            if isinstance(public, Mapping) and public.get("id") == checked["target_qid"]:
                target = deepcopy(dict(public))
                names = []
                for author in _claim_values(target, "P50"):
                    if not isinstance(author, Mapping):
                        continue
                    qid = author.get("id")
                    entity = (live_entities or {}).get(qid, {})
                    if (
                        isinstance(entity, Mapping)
                        and entity.get("id") == qid
                        and "missing" not in entity
                        and type(entity.get("lastrevid")) is int
                        and entity["lastrevid"] > 0
                    ):
                        names.extend(
                            label["value"]
                            for label in entity.get("labels", {}).values()
                            if isinstance(label, Mapping) and isinstance(label.get("value"), str)
                        )
                target["_verified_author_names"] = names
        if target is not None or candidate["kind"] == "holding":
            try:
                make_choice(
                    raw,
                    candidate["candidate_key"],
                    candidate["source_hash"],
                    target=target,
                    evidence_uri=checked["evidence_uri"],
                    reason=checked["reason"],
                )
                current = True
                expected_authors = next(
                    (
                        set(qids)
                        for (record_id, name), qids in (approved_author_qids or {}).items()
                        if str(record_id) == cn
                        and _text(name).casefold() == _text(candidate.get("author")).casefold()
                    ),
                    set(),
                )
                if expected_authors and target is not None:
                    actual_authors = {
                        author.get("id")
                        for author in _claim_values(target, "P50")
                        if isinstance(author, Mapping)
                    }
                    current = actual_authors == expected_authors
            except ValueError:
                current = False
        result.append({"proof": checked, "target_identity_current": current})
    return result


def source_record_for_audit(item, raw, live_entities=None, approved_author_qids=None):
    reviewed = reviewed_source_choices(item, raw, live_entities, approved_author_qids)
    source = deepcopy(dict(raw))
    for row in reviewed:
        candidate = row["proof"]["candidate"]
        if candidate["kind"] == "holding" and row["target_identity_current"]:
            source["reviewed_holding_occurrence"] = candidate["source_key"]
    return prepare_record_for_pipeline(source), reviewed


def reviewed_catalog_map(reviewed):
    return {
        (
            row["proof"]["candidate"]["source_field"],
            row["proof"]["candidate"]["source_text"].strip(),
        ): row
        for row in reviewed
        if row["proof"]["candidate"]["kind"] == "catalog_reference"
    }
