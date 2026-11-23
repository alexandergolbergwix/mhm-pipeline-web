"""Read exact MARC person occurrences and NLI authority IDs from BIBFRAME."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from xml.etree import ElementTree

import httpx

from app.pipeline import marc_occurrences as marc
from app.pipeline.wikidata_gila_facts import _snak_value

logger = logging.getLogger(__name__)
_NS = {
    "bf": "http://id.loc.gov/ontologies/bibframe/",
    "bflc": "http://id.loc.gov/ontologies/bflc/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "mads": "http://www.loc.gov/mads/rdf/v1#",
}
_RDF = "{" + _NS["rdf"] + "}"
_PERSON_TYPE = _NS["bf"] + "Person"
_BASE = "https://open-na.hosted.exlibrisgroup.com/alma/972NNL_INST/bf/entity/instance"
_ENDPOINT = re.compile(rf"{re.escape(_BASE)}/([0-9]{{16,24}})")
_ALMA_RECORD = r"https://[a-z0-9]+\.alma\.exlibrisgroup\.com/bf/{kind}/{cn}\?institute=972NNL_INST"
_MARC_KEY = re.compile(r"(?P<tag>[17]00)(?P<indicators>.{2})\s*(?=\$)")
_SUBFIELD = re.compile(r"\$([a-z0-9])([^$]+)")
_ID = re.compile(
    r"https://open-(?:na|eu)\.hosted\.exlibrisgroup\.com/alma/972NNL_INST/authorities/(987[0-9]{13,21})"
    r"(?:\.jsonld)?"
)
_MAX_BYTES = 2_000_000


def frame_url(cn: str) -> str:
    return f"{_BASE}/{cn}"


def cited_frame_controls(claim: Mapping) -> set[str]:
    return {
        match[1] for ref in claim["references"] for snak in ref.get("snaks", {}).get("P854", [])
        if (match := _ENDPOINT.fullmatch(_snak_value(snak)))
    }


def _record_node(root, cn: str, node: str, kind: str):
    pattern = _ALMA_RECORD.format(kind=kind, cn=cn)
    matches = [
        element for element in root.findall(f"./bf:{node}", _NS)
        if re.fullmatch(pattern, element.get(f"{_RDF}about", ""))
    ]
    return matches[0] if len(matches) == 1 else None


def parse_frame(cn: str, payload: bytes) -> dict[str, frozenset[str]]:
    """Keep only fields with one explicit personal authority on the exact record."""
    if len(payload) > _MAX_BYTES or not _ENDPOINT.fullmatch(frame_url(cn)):
        return {}
    root = ElementTree.fromstring(payload)
    instance = _record_node(root, cn, "Instance", "instances")
    work = _record_node(root, cn, "Work", "works")
    if instance is None or work is None or not any(
        link.get(f"{_RDF}resource") == instance.get(f"{_RDF}about")
        for link in work.findall("bf:hasInstance", _NS)
    ):
        return {}
    collected: dict[str, set[str]] = {}
    for entry in work.findall("./bf:contribution/bf:Contribution", _NS):
        agents = entry.findall("./bf:agent/bf:Agent", _NS)
        if len(agents) != 1:
            continue
        agent = agents[0]
        key = agent.findtext("bflc:marcKey", namespaces=_NS)
        authority = agent.get(f"{_RDF}about", "")
        match = _ID.fullmatch(authority)
        if not key or not _MARC_KEY.match(key):
            continue
        values = collected.setdefault(key, set())
        if match and any(
            node.get(f"{_RDF}resource") == _PERSON_TYPE for node in agent.findall("rdf:type", _NS)
        ) and any(
            node.get(f"{_RDF}resource") == authority for node in agent.findall("mads:isIdentifiedByAuthority", _NS)
        ):
            values.add(match[1])
        else:
            values.add("")
    return {key: frozenset(values) for key, values in collected.items()}


def _row_key(row):
    return row["tag"], tuple(row["indicators"]), tuple(
        (sf["code"], sf["value"].strip()) for sf in row["subfields"] if sf["code"] not in {"8", "9"}
    )


def frame_person_id(row, occurrences, frame):
    """Match the selected source field, including indicators, role, and date."""
    signature = _row_key(row)
    if sum(_row_key(field) == signature for field, _ in occurrences) != 1:
        return None
    matches = frame_field_ids(row, frame)
    return next(iter(matches)) if len(matches) == 1 and "" not in matches else None


def frame_field_ids(row, frame) -> set[str]:
    signature = _row_key(row)
    matches = set()
    for key, ids in frame.items():
        match = _MARC_KEY.match(key)
        if not match:
            continue
        tail = key[match.end():]
        tokens = list(_SUBFIELD.finditer(tail))
        if "".join(token[0] for token in tokens) != tail:
            continue
        parts = tuple((token[1], token[2].strip()) for token in tokens if token[1] not in {"8", "9"})
        if (match["tag"], tuple(match["indicators"]), parts) == signature:
            matches.update(ids)
    return matches


async def read_bibframes(controls, progress=None):
    controls = sorted({cn for cn in controls if _ENDPOINT.fullmatch(frame_url(cn))})
    if not controls:
        return {}
    loaded = {}
    headers = {"User-Agent": "MHMPipeline/1.0 (https://mhm-pipeline.org; verify NLI BIBFRAME)"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=False, timeout=15) as client:
        async def read(cn):
            try:
                response = await client.get(frame_url(cn))
                response.raise_for_status()
                return cn, parse_frame(cn, response.content)
            except (httpx.HTTPError, ElementTree.ParseError) as exc:
                logger.info("NLI BIBFRAME unavailable for %s (%s).", cn, type(exc).__name__)
                return cn, {}

        for start in range(0, len(controls), 16):
            if progress:
                await progress(start, len(controls))
            pending = []
            for cn in controls[start:start + 16]:
                pending.append(asyncio.create_task(read(cn)))
                await asyncio.sleep(0.4)
            for cn, result in await asyncio.gather(*pending):
                if result:
                    loaded[cn] = result
        if progress:
            await progress(len(controls), len(controls))
    return loaded
