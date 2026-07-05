"""SPARQL exists-check reconciliation for HMO Wikibase items."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wikibase_entity_mapping import WikibaseEntityMapping
from app.routers.linked_data_explorer import run_wikibase_sparql
from app.settings import get_settings
from converter.wikibase.hmo_exporter import HMO_SOURCE_URI

logger = logging.getLogger(__name__)


class ReconciliationUnavailableError(Exception):
    """SPARQL lookup failed — fail closed, never treat as absent."""


@dataclass(frozen=True)
class ReconcileOutcome:
    found: bool
    wikibase_id: str | None = None
    message: str = ""


def _escape_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


async def _source_uri_property_pid(db: AsyncSession) -> str | None:
    row = (
        await db.execute(
            select(WikibaseEntityMapping.wikibase_id).where(
                WikibaseEntityMapping.run_id.is_(None),
                WikibaseEntityMapping.ontology_uri == HMO_SOURCE_URI,
            )
        )
    ).scalar_one_or_none()
    return row


async def reconcile_item(
    db: AsyncSession,
    source_uri: str,
) -> ReconcileOutcome:
    """Find a live Wikibase item by ``hmo_source_uri`` claim value."""
    pid = await _source_uri_property_pid(db)
    if not pid:
        return ReconcileOutcome(found=False, message="hmo_source_uri not mapped in schema")

    settings = get_settings()
    wikibase_url: str = getattr(settings, "wikibase_sparql_url", "")
    if not wikibase_url:
        raise ReconciliationUnavailableError("Wikibase SPARQL endpoint not configured")

    pid_num = pid[1:] if pid.startswith("P") else pid
    literal = _escape_literal(source_uri)
    query = (
        f"SELECT ?item WHERE {{ ?item wdt:P{pid_num} \"{literal}\" . }} LIMIT 1"
    )
    try:
        data = await run_wikibase_sparql(wikibase_url, query)
    except httpx.HTTPError as exc:
        logger.warning("Wikibase reconcile SPARQL lookup failed for %s: %s", source_uri, exc)
        raise ReconciliationUnavailableError(str(exc)) from exc

    bindings = data.get("results", {}).get("bindings", [])
    if not bindings:
        return ReconcileOutcome(found=False)

    item_uri = bindings[0].get("item", {}).get("value", "")
    qid_match = re.search(r"/(Q\d+)$", item_uri)
    if not qid_match:
        return ReconcileOutcome(found=False, message=f"unexpected item URI: {item_uri}")
    return ReconcileOutcome(found=True, wikibase_id=qid_match.group(1))
