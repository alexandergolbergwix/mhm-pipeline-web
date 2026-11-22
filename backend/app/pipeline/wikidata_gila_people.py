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
from app.pipeline.wikidata_gila_authority_reads import library_of_congress_identifier, mazal_authority_url, mazal_identifier
from app.pipeline.wikidata_gila_catalogue_authority import NLI_SRU, REVIEWED_SCRIBE_SOURCES, catalogue_authority_url
from app.pipeline.wikidata_gila_facts import _live_claims, _reference_status, _snak_value
from app.pipeline.wikidata_gila_gnd_evidence import gnd_identifier
from app.pipeline.wikidata_gila_identity_evidence import (
    _viaf_identifier, _viaf_reference_ids, live_source_match, viaf_proves_person,
)
from app.pipeline.wikidata_gila_scribe_colophons import (
    DELFOGIT_HEADING, DELFOGIT_PARMA, DELFOGIT_PENN, DELFOGIT_QID, DELFOGIT_WRONG_IDS,
    MILHAUD_COLUMBIA, MILHAUD_DS_ID, MILHAUD_HEADING, MILHAUD_QID, MISATTRIBUTION,
    reviewed_colophon_urls, reviewed_scribe_exclusion, reviewed_scribe_note,
)
from app.pipeline.wikidata_source_choices import _claim_values
from converter.authority.mazal_index import MazalIndex
from converter.wikidata.content_projection import _work_title_key
from converter.wikidata.item_models import WikidataStatement, merge_statement_references
from converter.wikidata.property_mapping import Q_ADDRESSEE_ROLE, Q_FORMER_OWNER_ROLE, Q_SENDER_ROLE, nli_reference

logger = logging.getLogger(__name__)
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
_DATED_AUTHORITY_ROLES = {"author", "scribe", "former owner", "sender", "recipient", "mentioned"}
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


def catalogue_mazal_names(records) -> set[str]:
    return {
        MazalIndex.normalize_name(name)
        for record in records for _, variants in _person_occurrences(record)
        for variant in variants for name in marc.values(variant, "a")
        if len(_heading(name).split()) >= 2
    }


def _work_target(manuscript: Mapping, entities: Mapping, title: str, cn: str) -> Mapping:
    if not title:
        return {}
    matches = {}
    for link in _live_claims(manuscript, "P1574"):
        target = entities.get(str(link["value"]), {})
        titles = {_work_title_key(claim["value"]) for claim in _live_claims(target, "P1476")}
        if title in titles and _reference_status([link], cn, live=True) == "source_bound":
            matches[link["value"]] = target
    return next(iter(matches.values())) if len(matches) == 1 else {}


def _qualifier_values(claim: Mapping, pid: str) -> list:
    return [row["value"] for row in claim["qualifiers"] if row["property"] == pid]


def _row_roles(row, variants):
    terms = [_relator(value) for variant in variants for code in ("e", "4") for value in marc.values(variant, code)]
    return list(dict.fromkeys(term for term in terms if term)) or [
        {"100": "author", "600": "subject"}.get(row["tag"], "unassigned"),
    ]


def _author_target(row, record, manuscript, entities, cn):
    titles = marc.values(row, "t")
    if len(titles) > 1:
        return {}
    title = titles[0] if titles else record.get("title")
    return _work_target(manuscript, entities, _work_title_key(title), cn)


def _actual_name(entity: Mapping, claim: Mapping) -> set[str]:
    return {
        _heading(value) for value in (
            *_qualifier_values(claim, "P1932"),
            *(row["value"] for row in entity.get("labels", {}).values()),
            *(row["value"] for rows in entity.get("aliases", {}).values() for row in rows),
        )
    }


def _matching_role_claims(role, names, source_qid, entity, entities, independently_proven=()):
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
        if value in independently_proven or names & _actual_name(candidate, claim):
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


def dated_lc_match(candidate, names, dates, lc_headings):
    """Require an exact personal-name heading and both MARC life dates."""
    return _dated_authority_match(
        candidate, names, dates, (lc_headings or {}).get(library_of_congress_identifier(candidate), ()),
    )


def dated_mazal_match(candidate, names, dates, mazal_headings):
    return _dated_authority_match(
        candidate, names, dates, (mazal_headings or {}).get(mazal_identifier(candidate), ()),
    )


def _dated_authority_match(candidate, names, dates, headings):
    if not dates or not headings or "Q5" not in _links(candidate, "P31"):
        return False
    birth, death = dates
    for label in headings:
        match = re.fullmatch(
            r"(.+?)[, ]+([12][0-9]{3})(?: or ([12][0-9]{3}))?\s*[-–־]\s*([12][0-9]{3})\.?\s*",
            label,
        )
        if match and birth in (match[2], match[3]) and death == match[4] and _heading(match[1]) in names:
            return True
    return False


def _dated_authority_urls(candidate, names, dates, lc_headings, mazal_headings):
    """Return each exact authority that can substantiate the name and dates."""
    urls = []
    if dated_lc_match(candidate, names, dates, lc_headings):
        urls.append(f"https://id.loc.gov/authorities/names/{library_of_congress_identifier(candidate)}.html")
    if dated_mazal_match(candidate, names, dates, mazal_headings):
        urls.append(mazal_authority_url(mazal_identifier(candidate)))
    return urls


def _cites_authority(claim, urls):
    return any(
        snak.get("datavalue", {}).get("value") in urls
        for reference in claim["references"] for snak in reference.get("snaks", {}).get("P854", [])
    )


def _colophon_scribe_urls(candidate, record, cn, occurrence, display):
    if "Q5" not in _links(candidate, "P31"):
        return ()
    if proof := reviewed_scribe_note(record, cn, occurrence, display, candidate):
        return proof.person.urls
    urls = reviewed_colophon_urls(record, cn, occurrence, display)
    if not urls:
        return ()
    if display == MILHAUD_HEADING:
        return urls if (
            candidate.get("id") == MILHAUD_QID
            and _claim_values(candidate, "P13311") == [MILHAUD_DS_ID]
            and MILHAUD_COLUMBIA in _claim_values(candidate, "P973")
        ) else ()
    if display != DELFOGIT_HEADING or candidate.get("id") != DELFOGIT_QID:
        return ()
    if any(value in _claim_values(candidate, pid) for pid, value in DELFOGIT_WRONG_IDS.items()):
        return ()
    if not any(
        claim["value"] == "Q916292" and all(_cites_authority(claim, (url,)) for url in (DELFOGIT_PENN, DELFOGIT_PARMA))
        for claim in _live_claims(candidate, "P106")
    ):
        return ()
    return urls


def _confirmed_scribe_exclusion(manuscript, cn, display, exclusion):
    active = _live_claims(manuscript, "P11603")
    if any(
        claim["value"] == exclusion.person_qid or display in _qualifier_values(claim, "P1932")
        for claim in active
    ) or not any(
        _qualifier_values(claim, "P1932") == [exclusion.retained_heading]
        and (exclusion.retained_qid is None or claim["value"] == exclusion.retained_qid)
        and _reference_status([claim], cn, live=True) == "source_bound"
        for claim in active
    ):
        return False
    return any(
        claim.get("rank") == "deprecated" and claim.get("mainsnak", {}).get("snaktype") == "somevalue"
        and [_snak_value(snak) for snak in claim.get("qualifiers", {}).get("P1932", [])] == [display]
        and any(_snak_value(snak) == MISATTRIBUTION for snak in claim.get("qualifiers", {}).get("P2241", []))
        and _reference_status([claim], cn, live=True) == "source_bound"
        and _cites_authority({"references": claim.get("references", [])}, (exclusion.reference_url,))
        for claim in manuscript.get("claims", {}).get("P11603", [])
    )


def _proves_dated_role(claim, candidate, role, names, dates, lc_headings, mazal_headings, viaf_sources):
    cited_viaf = _viaf_reference_ids(claim["references"])
    if cited_viaf & (viaf_sources or {}).keys():
        return False
    urls = _dated_authority_urls(candidate, names, dates, lc_headings, mazal_headings)
    if not urls:
        return False
    if role != "scribe" or _cites_authority(claim, urls):
        return True
    return _viaf_identifier(candidate) in cited_viaf and dated_mazal_match(candidate, names, dates, mazal_headings)


def _scribe_death_year_url(candidate, source_dates, lc_death_years):
    if len(source_dates) != 1:
        return None
    match = re.fullmatch(r"(?:נפטר|died)\s+([12][0-9]{3})\.?", next(iter(source_dates)))
    identifier = library_of_congress_identifier(candidate)
    if not match or not identifier or (lc_death_years or {}).get(identifier) != match[1]:
        return None
    deaths = _claim_values(candidate, "P570")
    if len(deaths) != 1 or not isinstance(deaths[0], Mapping):
        return None
    death = deaths[0]
    if (
        not str(death.get("time", "")).startswith(f"+{match[1]}-")
        or death.get("precision", 0) < 9
        or death.get("before") != 0 or death.get("after") != 0
        or death.get("calendarmodel") != "http://www.wikidata.org/entity/Q1985727"
    ):
        return None
    return f"https://id.loc.gov/authorities/names/{identifier}.html"


def _gnd_scribe_url(candidate, names, dates, sources):
    identifier = gnd_identifier(candidate)
    record = (sources or {}).get(identifier)
    if not record or not dates or "Q5" not in _links(candidate, "P31"):
        return None
    if (
        record.get("gndIdentifier") != identifier or record.get("id") != f"https://d-nb.info/gnd/{identifier}"
        or "DifferentiatedPerson" not in record.get("type", [])
        or record.get("dateOfBirth") != [dates[0]] or record.get("dateOfDeath") != [dates[1]]
    ):
        return None
    headings = {
        _heading(name) for name in [record.get("preferredName"), *record.get("variantName", [])]
        if isinstance(name, str)
    }
    if not any(name in headings and len(name.split()) >= 2 for name in names):
        return None
    return record["id"]


def _matches_reviewed_scribe_fields(variants, proof):
    names = {_heading(" ".join(marc.values(field, "a") + marc.values(field, "c"))) for field in variants}
    dates = {value.strip() for field in variants for value in marc.values(field, "d") if value.strip()}
    return proof.heading in names and dates == {proof.dates}


def _reviewed_scribe_url(candidate, cn, identifier, row, variants, occurrences, live, catalogue_records):
    proof = REVIEWED_SCRIBE_SOURCES.get((cn, identifier))
    if not proof or proof.person_qid != candidate.get("id"):
        return None
    for rows in (occurrences, _person_occurrences(live)):
        matching_scribes = [
            (field, alternate) for field, alternate in rows
            if _matches_reviewed_scribe_fields(alternate, proof) and "scribe" in _row_roles(field, alternate)
        ]
        if matching_scribes != [(row, variants)]:
            return None
    if not proof.corroborating_control:
        return proof.url
    alternate = catalogue_records.get((proof.corroborating_control, identifier), {})
    main_entries = [fields for field, fields in _person_occurrences(alternate) if field["tag"] == "100"]
    if len(main_entries) != 1 or not _matches_reviewed_scribe_fields(main_entries[0], proof):
        return None
    return catalogue_authority_url(proof.corroborating_control, identifier)


def _catalogue_role_urls(candidate, cn, row, variants, occurrences, mazal_people, catalogue_records, *,
                         scribe=False, lc_death_years=None):
    identifier = mazal_identifier(candidate)
    profile = (mazal_people or {}).get(identifier)
    live = (catalogue_records or {}).get((cn, identifier))
    if not profile or not live or "Q5" not in _links(candidate, "P31") or row["tag"] not in {"100", "700"}:
        return ()
    if any(marc.values(v, code) for v in variants for code in ("0", "1", "3", "5", "j", "n", "p")):
        return ()
    catalogue_url = catalogue_authority_url(cn, identifier)
    if scribe and (url := _reviewed_scribe_url(
        candidate, cn, identifier, row, variants, occurrences, live, catalogue_records,
    )):
        return (catalogue_url, url)
    preferred = {_heading(name) for name in profile.preferred_names if len(_heading(name).split()) >= 2}
    for rows in (occurrences, _person_occurrences(live)):
        preferred_rows = [
            (field, alternate) for field, alternate in rows
            if preferred & {_heading(name) for variant in alternate for name in marc.values(variant, "a")}
        ]
        matched = preferred_rows or [
            (field, alternate) for field, alternate in rows
            if profile.indexed_names & {
                MazalIndex.normalize_name(name) for variant in alternate for name in marc.values(variant, "a")
                if len(_heading(name).split()) >= 2
            }
        ]
        if matched != [(row, variants)]:
            return ()
    source_dates = {value.strip() for v in variants for value in marc.values(v, "d") if value.strip()}
    if source_dates and source_dates != {profile.dates} and not _dated_authority_match(
        candidate, preferred, _exact_life_dates(variants),
        {f"{name} {profile.dates}" for name in profile.preferred_names},
    ):
        death_url = _scribe_death_year_url(candidate, source_dates, lc_death_years)
        return (catalogue_url, death_url) if death_url else ()
    return (catalogue_url,)


def _sole_work_for_added_author(row, record, manuscript, occurrences):
    indicators = row.get("indicators", ())
    if row["tag"] != "700" or len(indicators) != 2 or indicators[1] not in {"", " "}:
        return False
    if len({claim["value"] for claim in _live_claims(manuscript, "P1574")}) != 1:
        return False
    if [field for field, variants in occurrences if "author" in _row_roles(field, variants)] != [row]:
        return False
    fields = marc.occurrences(record)
    return (
        sum(field["tag"] == "245" for field in fields) == 1
        and not any(
            field["tag"] in {"505", "730", "740", "773", "774"}
            or (field["tag"] in {"700", "710", "711", "800", "810", "811"} and marc.values(field, "t"))
            for field in fields
        )
    )


def _separate_scribe_claim(claim, cn, selected, source_names, occurrences):
    names = {_heading(name) for name in _qualifier_values(claim, "P1932")}
    if len(names) != 1 or names & source_names or _reference_status([claim], cn, live=True) != "source_bound":
        return False
    matches = [
        field for field, variants in occurrences
        if any(_heading(name) in names for variant in variants for name in marc.values(variant, "a"))
        and "scribe" in _row_roles(field, variants)
        and not any(marc.values(variant, "j") for variant in variants)
    ]
    return len(matches) == 1 and matches[0] != selected


def plan_person_role_repair(
    raw, case, manuscript, entities, candidate, lc_headings, *, mazal_headings=None,
    mazal_people=None, catalogue_records=None, lc_death_years=None, gnd_sources=None,
):
    """Return an exact target, statement and optional claim ID for a checked role."""
    record = prepare_record_for_pipeline(dict(raw))
    cn = canonical_control_number(record["_control_number"])
    role = case["person_role"]
    pid = {"author": "P50", "scribe": "P11603", "former owner": "P3342"}.get(role)
    if not pid or (
        cn != case["control_number"] or manuscript.get("id") != case["qid"]
        or set(_claim_values(manuscript, "P3959")) != {cn}
    ):
        return None
    occurrences = _person_occurrences(record)
    source = [(row, variants) for row, variants in occurrences
              if marc.source_key(row) == case["occurrence"]]
    if len(source) != 1:
        return None
    row, variants = source[0]
    names = [name for variant in variants for name in marc.values(variant, "a")]
    source_names = {_heading(name) for name in names}
    if (
        len(marc.values(row, "a")) != 1 or role not in _row_roles(row, variants)
        or any("?" in name or "？" in name for name in names)
        or any(marc.values(variant, code) for variant in variants for code in ("0", "1", "3", "5", "j", "n", "p"))
    ):
        return None
    display = names[0].strip().rstrip(",")
    if not display or display != case["source_value"]:
        return None
    occurrence = marc.source_key(row)
    if role == "scribe" and reviewed_scribe_exclusion(record, cn, occurrence, display):
        return None
    dates = _exact_life_dates(variants)
    urls = _dated_authority_urls(candidate, source_names, dates, lc_headings, mazal_headings)
    gnd_url = _gnd_scribe_url(candidate, source_names, dates, gnd_sources) if role == "scribe" else None
    if gnd_url:
        urls.append(gnd_url)
    has_dated_proof = bool(urls)
    catalogue_urls = _catalogue_role_urls(
        candidate, cn, row, variants, occurrences, mazal_people, catalogue_records,
        scribe=role == "scribe", lc_death_years=lc_death_years if role == "scribe" else None,
    )
    colophon_urls = _colophon_scribe_urls(candidate, record, cn, occurrence, display) if role == "scribe" else ()
    reviewed_note = reviewed_scribe_note(record, cn, occurrence, display, candidate) if colophon_urls else None
    accepted_placeholder = (reviewed_note.former_heading if reviewed_note else None) or display
    required_urls = colophon_urls
    if role == "author" and row["tag"] != "100" and not marc.values(row, "t"):
        if not catalogue_urls or not _sole_work_for_added_author(row, record, manuscript, occurrences):
            return None
        required_urls = catalogue_urls
    elif not urls and not required_urls:
        required_urls = catalogue_urls
    if not urls and not required_urls:
        return None
    target = _author_target(row, record, manuscript, entities, cn) if role == "author" else manuscript
    if not target.get("id") or target["id"] == candidate["id"]:
        return None

    qualifiers = [{"property": "P1932", "value": display, "type": "string"}]
    role_qualifier = _ROLE_QUALIFIERS.get(role)
    if role_qualifier:
        qualifiers.append({"property": "P3831", "value": role_qualifier, "type": "item"})
    existing = []
    unknowns = []
    for claim in _live_claims(target, pid):
        if role_qualifier and _qualifier_values(claim, "P3831") != [role_qualifier]:
            if claim["value"] == candidate["id"] and not _qualifier_values(claim, "P3831"):
                return None
            continue
        placeholder_names = _qualifier_values(claim, "P1932") if claim["value"] == "somevalue" else []
        if placeholder_names and (
            any(_heading(name) in source_names for name in placeholder_names)
            or (bool(colophon_urls) and accepted_placeholder in placeholder_names)
        ):
            if colophon_urls:
                if (
                    not claim["claim_id"] or placeholder_names != [accepted_placeholder]
                    or {qualifier["property"] for qualifier in claim["qualifiers"]} != {"P1932"}
                    or _reference_status([claim], cn, live=True) != "source_bound"
                ):
                    return None
                unknowns.append(claim)
            continue
        if claim["value"] != candidate["id"]:
            if role == "scribe" and (catalogue_urls or gnd_url or colophon_urls) and _separate_scribe_claim(
                claim, cn, row, source_names, occurrences,
            ):
                continue
            if role != "former owner" or claim["value"] in {"somevalue", "novalue"}:
                return None
            continue
        if (
            not claim["claim_id"]
            or any(qualifier["property"] not in {"P1932", "P3831"} for qualifier in claim["qualifiers"])
            or any(_heading(name) not in source_names for name in _qualifier_values(claim, "P1932"))
        ):
            return None
        existing.append(claim)
    if len(existing) + len(unknowns) > 1:
        return None
    if existing and _reference_status(existing, cn, live=True) == "source_bound":
        cited = (
            all(_cites_authority(existing[0], (url,)) for url in required_urls)
            if required_urls else _cites_authority(existing[0], urls)
        )
        if cited or (role != "scribe" and has_dated_proof):
            return None

    selected = existing or unknowns
    references = [] if selected and _reference_status(selected, cn, live=True) == "source_bound" else nli_reference(cn)
    statement = WikidataStatement(pid, candidate["id"], "item", qualifiers=qualifiers, references=references)
    for url in required_urls or (urls[0],):
        merge_statement_references(statement, WikidataStatement(
            pid, candidate["id"], "item", references=[{"property": "P854", "value": url, "type": "url"}],
        ))
    return target["id"], statement, selected[0]["claim_id"] if selected else None


def people_role_cases(
    raw, manuscript, live_entities, *, authority_rows=(), viaf_sources=None, gnd_sources=None,
    lc_headings=None, lc_death_years=None, mazal_headings=None, mazal_people=None, catalogue_records=None,
):
    """Report one check per MARC occurrence and role."""
    record = prepare_record_for_pipeline(dict(raw))
    cn = canonical_control_number(record["_control_number"])
    manuscript = manuscript or {}
    entities = live_entities or {}
    exact_manuscript = set(_claim_values(manuscript, "P3959")) == {cn}
    cases = []
    occurrences = _person_occurrences(record)
    for row, variants in occurrences:
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
        roles = _row_roles(row, variants)
        source_ids = _authority_ids(variants)
        source_qids = {
            match[1] for variant in variants for code in ("0", "1") for value in marc.values(variant, code)
            if (match := re.fullmatch(r"(?:https://www\.wikidata\.org/(?:entity|wiki)/)?(Q[1-9][0-9]*)", value.strip()))
        }
        source_qid = next(iter(source_qids)) if len(source_qids) == 1 else None
        uncertain = any(mark in display for mark in ("?", "？")) or any(marc.values(v, "j") for v in variants)
        occurrence = marc.source_key(row)
        for role in roles:
            if role == "author":
                target = _author_target(row, record, manuscript, entities, cn) if exact_manuscript else {}
            else:
                target = manuscript if exact_manuscript else {}
            authority_proven = set()
            if role in _DATED_AUTHORITY_ROLES and not uncertain and not source_ids and not source_qids:
                pid = "P50" if role == "author" else "P11603" if role == "scribe" else "P3342"
                authority_proven = {
                    claim["value"] for claim in _live_claims(target, pid)
                    if _proves_dated_role(
                        claim, candidate := entities.get(claim["value"], {}), role, names, life_dates,
                        lc_headings, mazal_headings, viaf_sources,
                    ) or (
                        (role == "scribe" and (url := _gnd_scribe_url(candidate, names, life_dates, gnd_sources)))
                        and _cites_authority(claim, (url,))
                    ) or (
                        (role == "scribe" and (urls := _colophon_scribe_urls(candidate, record, cn, occurrence, display)))
                        and all(_cites_authority(claim, (url,)) for url in urls)
                    ) or (
                        (urls := _catalogue_role_urls(
                            candidate, cn, row, variants, occurrences, mazal_people, catalogue_records,
                            scribe=role == "scribe",
                            lc_death_years=lc_death_years if role == "scribe" else None,
                        ))
                        and all(_cites_authority(claim, (url,)) for url in urls)
                    )
                }
            matches = [
                (claim, candidate) for claim, candidate in
                _matching_role_claims(role, names, source_qid, target, entities, authority_proven)
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
                    or claim["value"] in authority_proven
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
            exclusion_proof = reviewed_scribe_exclusion(record, cn, occurrence, display) if role == "scribe" else None
            exclusion = bool(
                exact_manuscript and exclusion_proof and _confirmed_scribe_exclusion(target, cn, display, exclusion_proof)
            )
            reason = (
                "verified_scribe_misattribution" if exclusion else
                "recorded_person_uncertainty" if preserved_uncertainty else
                "verified_person_authority" if verified_link else
                "named_scribe_identity_unresolved" if role == "scribe" and not uncertain else
                "person_authority_needs_evidence" if role_present else "person_role_needs_evidence"
            )
            cases.append({
                "point": 5, "field": marc.source_key(row), "source_field": marc.source_key(row),
                "occurrence": marc.source_key(row), "source_value": display, "person_role": role,
                "classification": "identity_or_scope_uncertain", "reason": reason,
                "verified": verified_link or preserved_uncertainty or exclusion,
                "authority_verified": verified_link, "role_verified": role_present and (not uncertain or preserved_uncertainty),
            })
    return cases
