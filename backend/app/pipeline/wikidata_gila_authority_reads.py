"""Read the authority records used to verify published manuscript roles."""

import asyncio
import logging
import re

import httpx

from app.pipeline.wikidata_reference_reader import WIKIDATA_PUBLIC_USER_AGENT
from app.pipeline.wikidata_source_choices import _claim_values
from converter.wikidata.retry_policy import retry_after_seconds

logger = logging.getLogger(__name__)
_MADS = "http://www.loc.gov/mads/rdf/v1#"
_LCCN = re.compile(r"n[a-z]?[0-9]{6,12}\Z")
_VIAF_ID = re.compile(r"[0-9]{1,20}\Z")
_MAZAL_ID = re.compile(r"987[0-9]{6,17}\Z")
_VIAF_RETRY_BUDGET = 8.0
_VIAF_INTERVAL = 0.5


async def read_viaf_clusters(client: httpx.AsyncClient, identifiers: set[str]) -> dict[str, dict]:
    """Make one bounded retry and defer the rest of the batch on a continued 429."""
    clusters = {}
    for identifier in sorted(identifiers):
        if not _VIAF_ID.fullmatch(identifier):
            continue
        url = f"https://viaf.org/viaf/{identifier}"
        try:
            response = await client.get(url)
            if response.status_code == 429:
                delay = max(_VIAF_INTERVAL, retry_after_seconds(response.headers.get("Retry-After")))
                logger.info("VIAF authority audit received HTTP 429 with a %.1f second delay", delay)
                if delay > _VIAF_RETRY_BUDGET:
                    break
                await asyncio.sleep(delay)
                response = await client.get(url)
                if response.status_code == 429:
                    break
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                cluster = payload.get("ns1:VIAFCluster", payload)
                if isinstance(cluster, dict) and str(cluster.get("ns1:viafID") or cluster.get("viafID")) == identifier:
                    clusters[identifier] = cluster
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("VIAF authority audit could not read %s (%s)", identifier, type(exc).__name__)
        await asyncio.sleep(_VIAF_INTERVAL)
    return clusters


def library_of_congress_identifier(entity) -> str | None:
    values = _claim_values(entity, "P244")
    if len(values) == 1 and isinstance(values[0], str) and _LCCN.fullmatch(values[0]):
        return values[0]
    return None


def mazal_identifier(entity) -> str | None:
    values = _claim_values(entity, "P8189")
    if len(values) == 1 and isinstance(values[0], str) and _MAZAL_ID.fullmatch(values[0]):
        return values[0]
    return None


def mazal_authority_url(identifier: str) -> str:
    return f"https://www.nli.org.il/he/a-topic/{identifier}"


async def read_mazal_people(identifiers: set[str]) -> dict[str, tuple[frozenset[str], str]]:
    """Use only preferred headings from imported personal-name records."""
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from app.db import session_scope

    ids = sorted(identifier for identifier in identifiers if _MAZAL_ID.fullmatch(identifier))
    if not ids:
        return {}
    try:
        async with session_scope() as db:
            rows = await db.execute(text("""
                SELECT nli_id, preferred_name_heb, preferred_name_lat, dates
                FROM mazal_authorities
                WHERE nli_id = ANY(CAST(:ids AS text[]))
                  AND entity_type = 'person' AND main_marc_tag = '100'
            """), {"ids": ids})
            return {
                identifier: (
                    frozenset(name.strip() for name in (hebrew, latin) if name and name.strip()),
                    (dates or "").strip(),
                )
                for identifier, hebrew, latin, dates in rows
            }
    except SQLAlchemyError as exc:
        logger.warning("MAZAL authority records are unavailable for this audit (%s)", type(exc).__name__)
        return {}


def dated_mazal_headings(people: dict[str, tuple[frozenset[str], str]]) -> dict[str, frozenset[str]]:
    return {
        identifier: frozenset(f"{name} {dates}" for name in names)
        for identifier, (names, dates) in people.items() if dates
    }


async def read_mazal_headings(identifiers: set[str]) -> dict[str, frozenset[str]]:
    return dated_mazal_headings(await read_mazal_people(identifiers))


def library_of_congress_headings(payload, identifier: str) -> frozenset[str]:
    """Read only the exact personal name authority and its linked variants."""
    if not isinstance(payload, list):
        return frozenset()
    nodes = {row["@id"]: row for row in payload if isinstance(row, dict) and isinstance(row.get("@id"), str)}
    authority = nodes.get(f"http://id.loc.gov/authorities/names/{identifier}") or nodes.get(
        f"https://id.loc.gov/authorities/names/{identifier}", {},
    )
    types = authority.get("@type", [])
    if not isinstance(types, list) or not {_MADS + "Authority", _MADS + "PersonalName"} <= {
        value for value in types if isinstance(value, str)
    }:
        return frozenset()
    variants = [
        nodes.get(row.get("@id"), {})
        for row in authority.get(_MADS + "hasVariant", []) or []
        if isinstance(row, dict) and isinstance(row.get("@id"), str)
    ]
    return frozenset(
        row["@value"]
        for entity, field in [(authority, "authoritativeLabel"), *((node, "variantLabel") for node in variants)]
        for row in entity.get(_MADS + field, []) or []
        if isinstance(row, dict) and isinstance(row.get("@value"), str)
    )


async def read_library_of_congress_headings(
    client: httpx.AsyncClient, identifiers: set[str],
) -> dict[str, frozenset[str]]:
    records = {}
    for identifier in sorted(identifiers):
        if not _LCCN.fullmatch(identifier):
            continue
        try:
            response = await client.get(f"https://id.loc.gov/authorities/names/{identifier}.json")
            if response.status_code == 429:
                break
            response.raise_for_status()
            if headings := library_of_congress_headings(response.json(), identifier):
                records[identifier] = headings
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("Library of Congress authority audit could not read %s (%s)", identifier, type(exc).__name__)
    return records


def authority_audit_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=8, follow_redirects=False,
        headers={"User-Agent": WIKIDATA_PUBLIC_USER_AGENT, "Accept": "application/json"},
    )
