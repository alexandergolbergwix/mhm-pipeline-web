"""Import the exact NLI MARC source and audit each person role on that source."""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable, Mapping
from xml.etree import ElementTree as ET

import httpx

from app.pipeline import marc_occurrences as marc
from app.pipeline.marc_ingest import _parse_marcxml, prepare_record_for_pipeline
from app.pipeline.marc_verify_context import canonical_control_number
from app.pipeline.wikidata_gila_facts import _live_claims, _reference_status
from app.pipeline.wikidata_gila_identity_evidence import _viaf_identifier, live_source_match, viaf_proves_person
from app.pipeline.wikidata_source_choices import _claim_values
from converter.wikidata.content_projection import _work_title_key
from converter.wikidata.property_mapping import Q_ADDRESSEE_ROLE, Q_FORMER_OWNER_ROLE, Q_SENDER_ROLE

logger = logging.getLogger(__name__)
NLI_SRU = "https://nli.alma.exlibrisgroup.com/view/sru/972NNL_INST"
_MARC_NS = "{http://www.loc.gov/MARC21/slim}"
_BATCH_SIZE = 15
_CONTROL = re.compile(r"[0-9]{16,24}\Z")
_PERSON_TAGS = {"100", "600", "700", "800"}
_RELATORS = {
    "aut": "author", "author": "author", "מחבר": "author", "מחברת": "author",
    "scr": "scribe", "trc": "scribe", "scribe": "scribe", "transcriber": "scribe",
    "מעתיק": "scribe", "מעתיקה": "scribe",
    "fmo": "former owner", "former owner": "former owner", "בעלים קודמים": "former owner",
    "sender": "sender", "ממנו": "sender",
    "rcp": "recipient", "recipient": "recipient", "addressee": "recipient", "אליו": "recipient",
    "mentioned": "mentioned", "mentioned person": "mentioned", "מוזכר": "mentioned", "נזכר": "mentioned",
}
_ROLE_QUALIFIERS = {
    "former owner": Q_FORMER_OWNER_ROLE, "sender": Q_SENDER_ROLE, "recipient": Q_ADDRESSEE_ROLE,
}
_AUTHORITY_PATTERNS = (
    ("P214", r"(?:https?://(?:www\.)?viaf\.org/viaf/|\(VIAF\))([0-9]+)/?"),
    ("P8189", r"(?:https?://(?:www\.)?nli\.org\.il/(?:en|he)/(?:a-topic|authorities)/|\(NNL\))(987[0-9]+)/?"),
    ("P227", r"(?:https?://d-nb\.info/gnd/|\(DE-588\))([0-9X-]+)/?"),
    ("P244", r"(?:https?://id\.loc\.gov/authorities/names/|\(DLC\))([a-z][a-z0-9 ]+)/?"),
)


def parse_nli_records(payload: bytes, requested: set[str]) -> dict[str, dict]:
    """Reject a wrong identifier and keep complete standard MARC fields."""
    if len(payload) > 5_000_000 or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise ValueError("The NLI response is outside the MARCXML limits.")
    root = ET.fromstring(payload.decode("utf-8-sig"))
    if root.tag != "{http://www.loc.gov/zing/srw/}searchRetrieveResponse":
        raise ValueError("NLI did not return a catalogue search response.")
    records = {}
    local_fields = {}
    collection = ET.Element(f"{_MARC_NS}collection")
    for entry in root.findall(f".//{_MARC_NS}record"):
        identifiers = [node.text for node in entry.findall(f"{_MARC_NS}controlfield[@tag='001']")]
        if len(identifiers) != 1 or identifiers[0] not in requested or identifiers[0] in records:
            raise ValueError("NLI returned a duplicate or unexpected control number.")
        cn = identifiers[0]
        records[cn] = True
        for field in list(entry):
            if field.tag == f"{_MARC_NS}datafield" and not re.fullmatch(r"[0-9]{3}", field.get("tag", "")):
                local_fields.setdefault(cn, []).append(ET.tostring(field, encoding="unicode"))
                entry.remove(field)
        collection.append(entry)
    if not records:
        return {}
    parsed = _parse_marcxml(ET.tostring(collection, encoding="utf-8"))
    mapped = {canonical_control_number(row["_control_number"]): row for row in parsed}
    if mapped.keys() != records.keys():
        raise ValueError("The MARC parser did not retain each exact record.")
    for cn, fields in local_fields.items():
        mapped[cn]["nli_local_fields"] = fields
    return mapped


async def import_nli_people_marc(
    records: list[dict], progress: Callable[[int, int], Awaitable[None]],
) -> dict[str, dict]:
    sources = {}
    for record in records:
        cn = canonical_control_number(record.get("_control_number") or record.get("001"))
        if cn and (record.get("marc_occurrences") or record.get("fields")):
            sources[cn] = record
    controls = sorted({
        cn for record in records
        if _CONTROL.fullmatch(cn := canonical_control_number(record.get("_control_number") or record.get("001")))
        and cn not in sources
    })
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=15,
        headers={"User-Agent": "MHMPipeline/1.0 (https://mhm-pipeline.org; MARC source importer)"},
    ) as client:
        for offset in range(0, len(controls), _BATCH_SIZE):
            await progress(offset, len(controls))
            batch = controls[offset:offset + _BATCH_SIZE]
            try:
                response = await client.get(NLI_SRU, params={
                    "operation": "searchRetrieve", "version": "1.2", "recordSchema": "marcxml",
                    "query": " or ".join(f"alma.mms_id={cn}" for cn in batch),
                    "maximumRecords": len(batch),
                })
                response.raise_for_status()
                sources.update(await asyncio.to_thread(parse_nli_records, response.content, set(batch)))
            except (httpx.HTTPError, ET.ParseError, UnicodeError, ValueError) as exc:
                logger.warning("NLI MARC batch %d could not be read (%s)", offset, type(exc).__name__)
            finally:
                await asyncio.sleep(1)
    await progress(len(controls), len(controls))
    return sources


def _heading(value: object) -> str:
    text = unicodedata.normalize("NFC", str(value)).casefold()
    return " ".join(re.findall(r"[^\W_]+", text, flags=re.UNICODE))


def _links(entity: Mapping, pid: str) -> set[str]:
    return {str(value["id"]) for value in _claim_values(entity, pid) if isinstance(value, Mapping) and value.get("id")}


def _authority_ids(rows: list[dict]) -> set[tuple[str, str]]:
    return {
        (pid, match[1].replace(" ", ""))
        for row in rows for code in ("0", "1") for value in marc.values(row, code)
        for pid, pattern in _AUTHORITY_PATTERNS if (match := re.fullmatch(pattern, value.strip(), re.IGNORECASE))
    }


def _relator(value: str) -> str:
    term = value.casefold().strip().strip("().,").strip()
    match = re.fullmatch(r"https?://id\.loc\.gov/vocabulary/relators/([a-z]{3})/?", term)
    if match:
        term = match[1]
    return _RELATORS.get(term, term)


def _person_occurrences(record: Mapping) -> list[tuple[dict, list[dict]]]:
    rows = marc.occurrences(record)
    alternates = {
        value[:6]: row for row in rows if row["tag"] == "880"
        for value in marc.values(row, "6") if re.fullmatch(r"\d{3}-\d{2}(?:/.*)?", value)
    }
    result = []
    for row in rows:
        tag = row["tag"]
        if tag not in _PERSON_TAGS:
            continue
        variants = [alternates[f"{tag}-{value[4:6]}"] for value in marc.values(row, "6")
                    if re.fullmatch(r"880-\d{2}(?:/.*)?", value) and f"{tag}-{value[4:6]}" in alternates]
        result.append((row, [row, *variants]))
    return result


def _work_target(manuscript: Mapping, entities: Mapping, title: str, cn: str) -> Mapping:
    if not title:
        return {}
    matches = []
    for link in _live_claims(manuscript, "P1574"):
        target = entities.get(str(link["value"]), {})
        titles = {_work_title_key(claim["value"]) for claim in _live_claims(target, "P1476")}
        if title in titles and _reference_status([link], cn, live=True) == "source_bound":
            matches.append(target)
    return matches[0] if len(matches) == 1 else {}


def _qualifier_values(claim: Mapping, pid: str) -> list:
    return [row["value"] for row in claim["qualifiers"] if row["property"] == pid]


def _actual_name(entity: Mapping, claim: Mapping) -> set[str]:
    return {
        _heading(value) for value in (
            *_qualifier_values(claim, "P1932"),
            *(row["value"] for row in entity.get("labels", {}).values()),
            *(row["value"] for rows in entity.get("aliases", {}).values() for row in rows),
        )
    }


def _matching_role_claims(role, names, source_qid, entity, entities):
    pid = "P50" if role == "author" else "P11603" if role == "scribe" else "P3342"
    if role not in {"author", "scribe", "mentioned", *_ROLE_QUALIFIERS}:
        return []
    result = []
    for claim in _live_claims(entity, pid):
        value = claim["value"]
        candidate = entities.get(str(value), {})
        if source_qid and source_qid != value:
            continue
        role_values = _qualifier_values(claim, "P3831")
        if role in _ROLE_QUALIFIERS and role_values != [_ROLE_QUALIFIERS[role]]:
            continue
        if role == "mentioned" and role_values:
            continue
        if names & _actual_name(candidate, claim):
            result.append((claim, candidate))
    return result


def _exact_life_dates(variants):
    dates = {value.strip() for variant in variants for value in marc.values(variant, "d")}
    if len(dates) != 1:
        return None
    value = next(iter(dates))
    match = re.fullmatch(r"([12][0-9]{3})\s*[-–]\s*([12][0-9]{3})\.?", value)
    if not match or not 0 < int(match[2]) - int(match[1]) < 120:
        return None
    return match[1], match[2]


def people_role_cases(raw, manuscript, live_entities, *, authority_rows=(), viaf_sources=None, gnd_sources=None, lc_headings=None):
    """Report one check per MARC occurrence and role."""
    record = prepare_record_for_pipeline(dict(raw))
    cn = canonical_control_number(record["_control_number"])
    manuscript = manuscript or {}
    entities = live_entities or {}
    exact_manuscript = set(_claim_values(manuscript, "P3959")) == {cn}
    work = _work_target(manuscript, entities, _work_title_key(record.get("title")), cn) if exact_manuscript else {}
    cases = []
    for row, variants in _person_occurrences(record):
        names = marc.values(row, "a")
        if len(names) != 1:
            key = marc.source_key(row)
            cases.append({
                "point": 5, "source_field": key, "field": key, "occurrence": key,
                "source_value": " / ".join(names), "person_role": "unassigned",
                "classification": "identity_or_scope_uncertain", "reason": "person_heading_needs_evidence",
            })
            continue
        display = names[0].strip().rstrip(",")
        source_names = {name for variant in variants for name in marc.values(variant, "a")}
        names = {_heading(name) for name in source_names}
        life_dates = _exact_life_dates(variants)
        terms = [_relator(value) for variant in variants for code in ("e", "4") for value in marc.values(variant, code)]
        roles = list(dict.fromkeys(term for term in terms if term))
        if not roles:
            roles = ["author"] if row["tag"] == "100" else ["subject" if row["tag"] == "600" else "unassigned"]
        source_ids = _authority_ids(variants)
        source_qids = {
            match[1] for variant in variants for code in ("0", "1") for value in marc.values(variant, code)
            if (match := re.fullmatch(r"(?:https://www\.wikidata\.org/(?:entity|wiki)/)?(Q[1-9][0-9]*)", value.strip()))
        }
        source_qid = next(iter(source_qids)) if len(source_qids) == 1 else None
        uncertain = any(mark in display for mark in ("?", "？")) or any(marc.values(v, "j") for v in variants)
        for role in roles:
            title_values = marc.values(row, "t")
            if role == "author":
                target = (_work_target(manuscript, entities, _work_title_key(title_values[0]), cn)
                          if exact_manuscript and len(title_values) == 1 else work if not title_values else {})
            else:
                target = manuscript if exact_manuscript else {}
            matches = [
                (claim, candidate) for claim, candidate in
                _matching_role_claims(role, names, source_qid, target, entities)
                if _reference_status([claim], cn, live=True) == "source_bound"
            ]
            concrete = [pair for pair in matches if pair[0]["value"] not in {"somevalue", "novalue"}]
            if concrete and not uncertain:
                matches = concrete
            verified_link = False
            preserved_uncertainty = False
            role_present = len(matches) == 1
            if role_present and len(source_qids) <= 1:
                claim, candidate = matches[0]
                preserved_uncertainty = uncertain and claim["value"] == "somevalue" and bool(
                    _qualifier_values(claim, "P1480")
                    or any(mark in str(value) for mark in ("?", "？") for value in _qualifier_values(claim, "P1932"))
                )
                verified_link = not uncertain and "Q5" in _links(candidate, "P31") and bool(
                    (source_qid and candidate.get("id") == source_qid)
                    or (source_ids and all(value in _claim_values(candidate, pid) for pid, value in source_ids))
                )
                if (not verified_link and not uncertain and not source_ids and not source_qids
                    and life_dates and "Q5" in _links(candidate, "P31") and (viaf := _viaf_identifier(candidate))):
                    verified_link = any(viaf_proves_person(
                        (name, *life_dates), viaf, (viaf_sources or {}).get(viaf),
                    ) for name in source_names)
                if not verified_link and not uncertain and role == "scribe" and not source_ids and not source_qids:
                    verified_link = live_source_match(
                        raw, manuscript, entities, cn, scribe=True, name=display,
                        authority_rows=authority_rows, viaf_sources=viaf_sources, gnd_sources=gnd_sources,
                        lc_headings=lc_headings,
                    )
            reason = (
                "recorded_person_uncertainty" if preserved_uncertainty else
                "verified_person_authority" if verified_link else
                "named_scribe_identity_unresolved" if role == "scribe" and not uncertain else
                "person_authority_needs_evidence" if role_present else "person_role_needs_evidence"
            )
            cases.append({
                "point": 5, "field": marc.source_key(row), "source_field": marc.source_key(row),
                "occurrence": marc.source_key(row), "source_value": display, "person_role": role,
                "classification": "identity_or_scope_uncertain", "reason": reason,
                "verified": verified_link or preserved_uncertainty,
                "authority_verified": verified_link, "role_verified": role_present and (not uncertain or preserved_uncertainty),
            })
    return cases
