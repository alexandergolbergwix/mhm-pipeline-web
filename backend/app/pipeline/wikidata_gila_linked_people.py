"""Verify role identities from exact NLI Alma bibliographic links."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping

import httpx

from app.pipeline import marc_occurrences as marc
from app.pipeline.wikidata_gila_authority_reads import mazal_identifier
from app.pipeline.wikidata_gila_bibframe_people import frame_person_id, frame_url
from app.pipeline.wikidata_gila_facts import _reference_status, _snak_value
from converter.wikidata.property_mapping import ROLE_TO_PID

logger = logging.getLogger(__name__)
ALMA = "https://open-na.hosted.exlibrisgroup.com/alma/972NNL_INST"
CONTROL = re.compile(r"[0-9]{16,24}")
AUTHORITY_ID = re.compile(r"987[0-9]{13,21}")
_AUTHORITY_URL = re.compile(
    rf"https://open-(?:na|eu)\.hosted\.exlibrisgroup\.com/alma/972NNL_INST/authorities/"
    rf"({AUTHORITY_ID.pattern})(?:\.jsonld)?"
)
_BIB_URL = re.compile(rf"{re.escape(ALMA)}/bibs/({CONTROL.pattern})\.jsonld")
ROLE_PROPERTIES = {
    "author": "P50",
    "scribe": "P11603",
    "mentioned": "P3342",
    "מאייר": "P110",
    "former owner": "P3342",
    **{role: ROLE_TO_PID[role] for role in ("מעיר", "annotator", "חותם", "signatory", "illuminator")},
}
COPY_ROLE_PROPERTIES = frozenset(ROLE_PROPERTIES.values()) - {"P50"}
_ALMA_HEADERS = {"User-Agent": "MHMPipeline/1.0 (https://mhm-pipeline.org; verify NLI catalogue linkage)"}


def bib_url(cn: str) -> str:
    return f"{ALMA}/bibs/{cn}.jsonld"


def cited_bib_controls(claim: Mapping) -> set[str]:
    return {
        match[1] for reference in claim["references"]
        for snak in reference.get("snaks", {}).get("P854", [])
        if (match := _BIB_URL.fullmatch(str(_snak_value(snak) or "")))
    }


async def read_linked_bibs(controls, progress=None) -> dict[str, dict]:
    """Read each exact public bib once, with a bounded rate across requests."""
    requested = sorted({cn for cn in controls if CONTROL.fullmatch(cn)})
    if not requested:
        return {}
    loaded = {}
    processed = 0
    async with httpx.AsyncClient(headers=_ALMA_HEADERS, timeout=15, follow_redirects=False) as client:
        async def read(cn):
            url = bib_url(cn)
            try:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and data.get("@id") in {url, url.removesuffix(".jsonld")}:
                    return cn, data
                logger.info("NLI linked bibliography ID differs for %s.", cn)
            except (httpx.HTTPError, ValueError) as error:
                logger.info("NLI linked bibliography unavailable for %s (%s).", cn, type(error).__name__)
            return cn, None

        for offset in range(0, len(requested), 20):
            if progress:
                await progress(processed, len(requested))
            tasks = set()
            for cn in requested[offset:offset + 20]:
                tasks.add(asyncio.create_task(read(cn)))
                await asyncio.sleep(0.4)
            for cn, data in await asyncio.gather(*tasks):
                if data is not None:
                    loaded[cn] = data
                processed += 1
        if progress:
            await progress(processed, len(requested))
    return loaded


def _name_keys(variants):
    # Full heading components distinguish titles, numeration, and dates.
    from app.pipeline.wikidata_gila_people import _heading

    keys = set()
    for variant in variants:
        name = " ".join(value for code in ("a", "b", "c", "q") for value in marc.values(variant, code))
        dates = marc.values(variant, "d")
        if len(_heading(name).split()) < 2 or len(dates) > 1:
            continue
        keys.add(_heading(f"{name} {' '.join(dates)}"))
    return keys


def _name_without_years(heading):
    return re.sub(r" (?:[12][0-9]{3})(?: [12][0-9]{3})?$", "", heading)


def _eligible_role(row, variants, role) -> bool:
    """Return one first-party ID for one exact, unqualified MARC occurrence."""
    from app.pipeline.wikidata_gila_people import _row_roles

    if role not in ROLE_PROPERTIES or role not in _row_roles(row, variants):
        return False
    if row["tag"] not in ({"100", "700"} if role == "author" else {"700"}):
        return False
    if any(marc.values(v, c) for v in variants for c in ("0", "1", "3", "5", "j", "n", "p", "u", "x", "y", "z")):
        return False
    if role != "author" and (row["indicators"][1] == "2" or any(marc.values(v, "t") for v in variants)):
        return False
    if any(re.search(r"[?？]", name) for variant in variants for name in marc.values(variant, "a")):
        return False
    return True


def linked_role_id(cn, row, variants, occurrences, role, bibs, *, record) -> str | None:
    """Return one first-party ID for one exact, unqualified MARC occurrence."""
    from app.pipeline.wikidata_gila_people import _heading

    if not _eligible_role(row, variants, role):
        return None
    keys = _name_keys(variants)
    stems = {_name_without_years(key) for key in keys}
    if not keys or any(
        stems & {_name_without_years(key) for key in _name_keys(other_variants)}
        for other, other_variants in occurrences if other != row
    ):
        return None
    if role == "former owner":
        notes = {
            f" {_heading(note)} " for field in marc.occurrences(record)
            if field["tag"] == "561" and field.get("indicators", ["0"])[0] == "1"
            for note in marc.values(field, "a")
        }
        if not any(f" {stem} " in note for stem in stems for note in notes):
            return None
    data = bibs.get(cn, {})
    url = bib_url(cn)
    if data.get("@id") not in {url, url.removesuffix(".jsonld")}:
        return None
    matches = set()
    has_marc_dates = any(marc.values(v, "d") for v in variants)
    groups = ("creator", "contributor") if role == "author" else ("contributor",)
    for group in groups:
        entries = data.get(group, [])
        for entry in entries if isinstance(entries, list) else [entries]:
            if not isinstance(entry, dict) or not isinstance(entry.get("label"), str):
                continue
            key = _heading(entry["label"])
            if key not in keys and _name_without_years(key) not in stems:
                continue
            identifier = _AUTHORITY_URL.fullmatch(str(entry.get("@id", "")))
            if not identifier or (has_marc_dates and key not in keys):
                return None
            matches.add(identifier[1])
    return next(iter(matches)) if len(matches) == 1 else None


def linked_person_matches(candidate, authority_id):
    from app.pipeline.wikidata_gila_people import _links

    return bool(authority_id and mazal_identifier(candidate) == authority_id and "Q5" in _links(candidate, "P31"))


def has_explicit_linked_heading(variants, bib):
    from app.pipeline.wikidata_gila_people import _heading

    stems = {_name_without_years(key) for key in _name_keys(variants)}
    for group in ("creator", "contributor"):
        entries = bib.get(group, [])
        for entry in entries if isinstance(entries, list) else [entries]:
            if (
                isinstance(entry, dict) and _AUTHORITY_URL.fullmatch(str(entry.get("@id", "")))
                and _name_without_years(_heading(entry.get("label") or "")) in stems
            ):
                return True
    return False


def role_authority(cn, row, variants, occurrences, role, *, record, bibs, frames):
    """Return the bound authority ID and the source that proves the exact field."""
    if not _eligible_role(row, variants, role):
        return None
    linked = linked_role_id(cn, row, variants, occurrences, role, bibs, record=record)
    exact = frame_person_id(row, occurrences, frames.get(cn, {}))
    if linked and exact and linked != exact:
        return None
    if linked:
        return linked, bib_url(cn)
    return (exact, frame_url(cn)) if exact else None


def has_linked_source(claim, cn, *urls):
    """Check that one reference block contains the source ID and all required URLs."""
    expected = set(urls or (bib_url(cn),))
    return any(
        expected <= {
            _snak_value(snak) for snak in reference.get("snaks", {}).get("P854", [])
        }
        and _reference_status([{"references": [reference]}], cn, live=True) == "source_bound"
        for reference in claim["references"]
    )
