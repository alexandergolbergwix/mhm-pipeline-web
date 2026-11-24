"""Prepare read-only exports bound to a completed public audit snapshot."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit

from app.pipeline import marc_occurrences as marc
from app.pipeline.wikidata_evidence_reads import paced_wikidata_evidence_reads
from app.pipeline.wikidata_gila_facts import _snak_value, _text
from app.pipeline.wikidata_reference_reader import ReferenceItemReader
from app.publication.reviewed_reference_scope import GILA_CREATOR_UID
from converter.wikidata.property_mapping import Q_KTIV, nli_catalog_url
from converter.wikidata.work_identity import reference_control_numbers

_QID = re.compile(r"Q[1-9][0-9]*")
_CHECK_FIELDS = ("case_id", "points", "control_number", "source_field", "occurrence",
                 "source_value", "person_role", "reason", "property", "expected_qualifiers",
                 "expected_precision", "expected_calendar", "expected_unit")


def verified_export(packet, additional_cases):
    manuscripts = {
        row["control_number"]: row for row in packet["checked_manuscripts"] if row["disposition"] == "verified"
    }
    checks = []
    for row in (*packet["cases"], *additional_cases):
        if row["disposition"] != "verified":
            continue
        cn = row.get("control_number")
        manuscript = manuscripts.get(cn, {})
        qid = row.get("verified_target_qid") or row.get("qid")
        revision = row.get("verified_target_revision") or row.get("live_revision")
        if qid == manuscript.get("qid"):
            revision = revision or manuscript.get("revision")
        if not _QID.fullmatch(qid or "") or type(revision) is not int or revision < 1:
            continue
        matches = []
        for match in row.get("verified_matches", []):
            statement_id = match.get("statement_id")
            if statement_id and statement_id.startswith(f"{qid}$"):
                matches.append(match)
            elif row.get("property") == "description:en" and match.get("value"):
                matches.append({"value": match["value"]})
        checks.append({
            **{key: row[key] for key in _CHECK_FIELDS if key in row},
            "qid": qid, "revision": revision, "matches": matches,
            **({"source_url": nli_catalog_url(cn)} if cn else {}),
        })
    return {
        "manuscripts": list(manuscripts.values()), "verified_checks": checks,
        "matched_statement_count": len({
            match["statement_id"] for check in checks for match in check["matches"] if "statement_id" in match
        }),
    }


def _original_date_matches(case, records):
    expected = _text(case.get("source_value"))
    dates = {
        _text(value) for row in marc.occurrences(records.get(case["control_number"], {}))
        if row["tag"] in {"260", "264"}
        for value in marc.values(row, "c") if _text(value)
    }
    return bool(expected) and dates == {expected}


def _catalogue_references_match(claim, cn):
    references = claim.get("references", [])
    if not references:
        return False
    for ref in references:
        snaks = ref.get("snaks", {})
        if reference_control_numbers([ref]) != {cn} or set(snaks) - {"P248", "P854", "P3959", "P813"}:
            return False
        for url in snaks.get("P854", []):
            value = str(_snak_value(url) or "")
            host = urlsplit(value).hostname or ""
            if not (host == "nli.org.il" or host.endswith(".nli.org.il")) or cn not in value:
                return False
        if any(_snak_value(snak) != Q_KTIV for snak in snaks.get("P248", [])):
            return False
    return True


async def proven_claim_errors(packet, entities, records):
    candidates = {}
    for case in packet["cases"]:
        qid = case.get("qid")
        if not _QID.fullmatch(qid or "") or not case.get("contradictions"):
            continue
        entity = entities.get(qid, {})
        if (
            entity.get("id") != qid or type(entity.get("lastrevid")) is not int
            or entity.get("lastrevid") != case.get("live_revision")
        ):
            continue
        cn = case["control_number"]
        for row in case["contradictions"]:
            if row["error"] == "unsupported_circa_qualifier" and not _original_date_matches(case, records):
                continue
            statement_id = row["statement_id"]
            if not statement_id.startswith(f"{qid}$"):
                continue
            claim = next((
                raw for raw in entity.get("claims", {}).get(case["property"], [])
                if raw.get("id") == statement_id
            ), None)
            try:
                exact_source = (
                    claim is not None and claim.get("rank", "normal") in {"normal", "preferred"}
                    and _catalogue_references_match(claim, cn)
                )
            except (TypeError, ValueError):
                exact_source = False
            if not exact_source:
                continue
            candidates[(qid, statement_id, row["error"])] = {
                **row, "qid": qid, "revision": case["live_revision"], "control_number": cn,
                "property": case["property"], "source_field": case["source_field"],
                "source_text": case.get("source_value"), "source_url": nli_catalog_url(cn),
                "expected_value": case.get("expected_value"),
                "expected_qualifiers": case.get("expected_qualifiers", {}),
            }
    reader = ReferenceItemReader()

    async def creator(qid):
        try:
            return await reader.creator_uid(qid)
        except (ValueError, RuntimeError):
            return None

    owners = {}
    qids = sorted({row["qid"] for row in candidates.values()})
    with paced_wikidata_evidence_reads():
        for start in range(0, len(qids), 8):
            group = qids[start:start + 8]
            owners.update(zip(group, await asyncio.gather(*(creator(qid) for qid in group)), strict=True))
    return {
        "claims": [
            {**row, "first_creator_uid": GILA_CREATOR_UID}
            for row in candidates.values() if owners[row["qid"]] == GILA_CREATOR_UID
        ],
        "creator_checks_unavailable": sum(uid is None for uid in owners.values()),
    }
