"""SPARQL exists-check reconciliation for HMO Wikibase items."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wikibase_entity_mapping import (
    ENTITY_KIND_INSTANCE,
    WikibaseEntityMapping,
)
from app.routers.linked_data_explorer import run_wikibase_sparql
from app.settings import get_settings
from converter.wikibase.hmo_exporter import HMO_SOURCE_URI

logger = logging.getLogger(__name__)


def live_instance_qids_select():
    """The one definition of the live-instance ledger: every run, no ambiguity.

    Every reader and the writer MUST resolve ``source_uri -> wikibase_id``
    through this statement. Scoping it to a single run made the Studio table
    report items that another run had already uploaded as "new (not
    uploaded)" (Rule W-278): run ``47e03437`` built 18,480 items and owned zero
    mapping rows, so its joins found nothing while 18,466 of those source URIs
    already mapped to live QIDs under runs ``3494ebf5`` and ``48ba6c13``.

    Two scopes apply:

    * **All runs.** ``run_id IS NOT NULL`` — a mapping row written by any run
      is a live Wikibase item for every run. Instance rows with a NULL
      ``run_id`` are the *Wikidata Studio* QID ledger
      (``wikidata_qid_ledger.py``), whose keys are the separate
      ``wikidata:…`` namespace, so they are excluded here.
    * **No ambiguity.** A QID that two source URIs claim, or a source URI that
      carries two QIDs, is corrupt or replayed state. Trusting it updates the
      wrong live item; trusting the wrong side of it on read shows a curator a
      QID that is not this entity. Both directions drop out.
    """
    from sqlalchemy import and_, func, select

    scoped = and_(
        WikibaseEntityMapping.entity_kind == ENTITY_KIND_INSTANCE,
        WikibaseEntityMapping.run_id.is_not(None),
    )
    ambiguous_qid = (
        select(WikibaseEntityMapping.wikibase_id)
        .where(scoped)
        .group_by(WikibaseEntityMapping.wikibase_id)
        .having(func.count(func.distinct(WikibaseEntityMapping.ontology_uri)) > 1)
    )
    ambiguous_uri = (
        select(WikibaseEntityMapping.ontology_uri)
        .where(scoped)
        .group_by(WikibaseEntityMapping.ontology_uri)
        .having(func.count(func.distinct(WikibaseEntityMapping.wikibase_id)) > 1)
    )
    return (
        select(
            WikibaseEntityMapping.ontology_uri,
            WikibaseEntityMapping.wikibase_id,
        ).where(
            scoped,
            WikibaseEntityMapping.wikibase_id.not_in(ambiguous_qid),
            WikibaseEntityMapping.ontology_uri.not_in(ambiguous_uri),
        )
    )


async def load_live_instance_qids(db: AsyncSession) -> dict[str, str]:
    """``source_uri -> live Wikibase id``, as a dict.

    Same ledger as :func:`live_instance_qids_select`; use this where a dict is
    handier than a subquery.
    """
    rows = (await db.execute(live_instance_qids_select())).all()
    return {str(uri): str(qid) for uri, qid in rows}


class ReconciliationUnavailableError(Exception):
    """SPARQL lookup failed — fail closed, never treat as absent."""


@dataclass(frozen=True)
class ReconcileOutcome:
    found: bool
    wikibase_id: str | None = None
    message: str = ""


def _escape_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


# Ontology namespace history (Rule W-55). Items uploaded before the w3id move
# carry ``hmo_source_uri`` in the old namespace; reconcile must match both so a
# namespace migration never orphans a live item into a duplicate create.
_CURRENT_HMO_NS = "https://w3id.org/mhm/ontology#"
_LEGACY_HMO_NS = "http://www.ontology.org.il/HebrewManuscripts/2025-12-06#"


def _reconcile_uri_variants(source_uri: str) -> list[str]:
    """Every namespace variant of ``source_uri`` reconcile should match."""
    variants = [source_uri]
    if source_uri.startswith(_CURRENT_HMO_NS):
        legacy = _LEGACY_HMO_NS + source_uri[len(_CURRENT_HMO_NS):]
        variants.append(legacy)
    elif source_uri.startswith(_LEGACY_HMO_NS):
        current = _CURRENT_HMO_NS + source_uri[len(_LEGACY_HMO_NS):]
        variants.append(current)
    return variants


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


async def resolve_source_uri_pid(db: AsyncSession) -> str | None:
    """The ``hmo_source_uri`` property id, resolved once per caller.

    Schema-level (``run_id IS NULL``) and effectively constant for the
    lifetime of a job — a caller looping over thousands of entities
    (the item-upload job) should resolve this ONCE and pass it into
    every :func:`reconcile_item` call via ``pid=`` instead of paying a
    redundant identical query (and a redundant open/close DB
    transaction) per item.
    """
    return await _source_uri_property_pid(db)


async def reconcile_item(
    db: AsyncSession,
    source_uri: str,
    *,
    pid: str | None = None,
) -> ReconcileOutcome:
    """Find a live Wikibase item by ``hmo_source_uri`` claim value.

    ``pid`` should be pre-resolved via :func:`resolve_source_uri_pid`
    by callers that invoke this in a loop; a caller that omits it gets
    it looked up here (and the resulting transaction is immediately
    closed below), the same as before this function grew a fast path.
    """
    if pid is None:
        pid = await _source_uri_property_pid(db)
        # Close out this read-only transaction BEFORE the SPARQL call
        # below — a caller looping over thousands of entities without
        # pre-resolving ``pid`` would otherwise chain this straight into
        # a slow, retrying external HTTP call (Wikibase Cloud
        # create/update, up to ~4 minutes of backoff on a flaky
        # endpoint). Leaving the SELECT's transaction open across that
        # is exactly the "idle in transaction" hazard app.db's 2-minute
        # backstop exists to catch — better to never hold it open at all.
        await db.commit()
    if not pid:
        return ReconcileOutcome(found=False, message="hmo_source_uri not mapped in schema")

    settings = get_settings()
    wikibase_url: str = getattr(settings, "wikibase_sparql_url", "")
    if not wikibase_url:
        raise ReconciliationUnavailableError("Wikibase SPARQL endpoint not configured")

    pid_num = pid[1:] if pid.startswith("P") else pid
    # Use the instance's OWN direct-property URI, not the ``wdt:`` prefix — on
    # wikibase.cloud ``wdt:`` defaults to Wikidata's namespace so ``wdt:PNNN``
    # silently matches nothing (Rule W-56). Also match the source URI in BOTH
    # the current and any legacy ontology namespace so a Rule-W-55 namespace
    # migration doesn't orphan already-uploaded items (which still carry the
    # old-namespace ``hmo_source_uri``) → prevents duplicate creation.
    base = str(getattr(settings, "wikibase_cloud_base_url", "") or "").rstrip("/")
    if not base:
        base = wikibase_url.split("/query/sparql")[0].rstrip("/")
    direct = f"<{base}/prop/direct/P{pid_num}>"
    values = " ".join(f'"{_escape_literal(v)}"' for v in _reconcile_uri_variants(source_uri))
    query = (
        f"SELECT ?item WHERE {{ VALUES ?val {{ {values} }} "
        f"?item {direct} ?val . }} LIMIT 1"
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
