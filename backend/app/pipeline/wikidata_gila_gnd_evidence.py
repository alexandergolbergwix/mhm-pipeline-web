"""Read exact person records from the GND service."""

import asyncio
import logging
import re

import httpx

from app.pipeline.wikidata_reference_reader import WIKIDATA_PUBLIC_USER_AGENT
from app.pipeline.wikidata_source_choices import _claim_values

logger = logging.getLogger(__name__)
_GND_ID = r"[0-9]{7,10}[0-9X]?"


def gnd_identifier(entity):
    identifiers = _claim_values(entity, "P227")
    if len(identifiers) == 1 and re.fullmatch(_GND_ID, str(identifiers[0])):
        return identifiers[0]
    return None


def cited_gnd_ids(entity):
    return {
        match[1]
        for claim in entity.get("claims", {}).get("P11603", [])
        if claim.get("rank") != "deprecated"
        for block in claim.get("references", [])
        for snak in block.get("snaks", {}).get("P854", [])
        if isinstance((url := snak.get("datavalue", {}).get("value")), str)
        and (match := re.fullmatch(rf"https://d-nb\.info/gnd/({_GND_ID})", url))
    }


async def _read_record(client, identifier):
    url = f"https://lobid.org/gnd/{identifier}.json"
    for delay in (1, 2):
        try:
            return await client.get(url)
        except httpx.TransportError as exc:
            logger.info("Retry GND authority %s after %s.", identifier, type(exc).__name__)
            await asyncio.sleep(delay)
    return await client.get(url)


async def read_gnd_sources(identifiers):
    results = {}
    async with httpx.AsyncClient(
        timeout=8, follow_redirects=False, headers={"User-Agent": WIKIDATA_PUBLIC_USER_AGENT},
    ) as client:
        for identifier in sorted(identifiers):
            if not re.fullmatch(_GND_ID, identifier):
                continue
            response = await _read_record(client, identifier)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            record = response.json()
            if (
                isinstance(record, dict)
                and record.get("gndIdentifier") == identifier
                and record.get("id") == f"https://d-nb.info/gnd/{identifier}"
                and "DifferentiatedPerson" in record.get("type", [])
            ):
                results[identifier] = record
    return results
