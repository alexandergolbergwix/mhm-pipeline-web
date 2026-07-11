"""Global QID ledger for Wikidata Studio items (cross-run dedup)."""

from __future__ import annotations

import os
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wikibase_entity_mapping import ENTITY_KIND_INSTANCE, WikibaseEntityMapping


def ledger_namespace() -> str:
    if os.environ.get("WIKIDATA_TEST_MODE", "").lower() == "true":
        return "wikidata-test"
    return "wikidata"


def _entity_type(item: dict[str, Any] | Any) -> str:
    if isinstance(item, dict):
        return str(item.get("entity_type") or "other")
    return str(getattr(item, "entity_type", "") or "other")


def _local_id(item: dict[str, Any] | Any) -> str:
    if isinstance(item, dict):
        return str(item.get("local_id") or "")
    for attr in ("local_id", "id", "key"):
        v = getattr(item, attr, None)
        if v:
            return str(v)
    return ""


def ledger_key_for_item(
    item: dict[str, Any] | Any,
    ns: str | None = None,
) -> str:
    """Stable global ledger key: ``{wikidata|wikidata-test}:{marc|person|work}:{id}``."""
    namespace = ns or ledger_namespace()
    local_id = _local_id(item)
    etype = _entity_type(item)
    if etype == "manuscript":
        stable = local_id or _first_statement_value(item, "P3959") or "unknown"
        return f"{namespace}:marc:{stable}"
    if etype == "person":
        return f"{namespace}:person:{local_id or 'unknown'}"
    if etype == "work":
        return f"{namespace}:work:{local_id or 'unknown'}"
    return f"{namespace}:other:{local_id or 'unknown'}"


def lookup_ledger_qid(ledger: dict[str, str], key: str) -> str | None:
    qid = ledger.get(key)
    return str(qid) if qid else None


async def load_global_ledger(db: AsyncSession) -> dict[str, str]:
    """All global Wikidata Studio instance mappings (``run_id IS NULL``)."""
    rows = (
        await db.execute(
            select(
                WikibaseEntityMapping.ontology_uri,
                WikibaseEntityMapping.wikibase_id,
            ).where(
                WikibaseEntityMapping.run_id.is_(None),
                WikibaseEntityMapping.entity_kind == ENTITY_KIND_INSTANCE,
                WikibaseEntityMapping.ontology_uri.like("wikidata%"),
            )
        )
    ).all()
    return {uri: qid for uri, qid in rows}


async def record_ledger_mapping(
    db: AsyncSession,
    key: str,
    qid: str,
    *,
    local_key: str | None = None,
    label: str = "",
) -> None:
    existing = (
        await db.execute(
            select(WikibaseEntityMapping).where(
                WikibaseEntityMapping.ontology_uri == key,
                WikibaseEntityMapping.entity_kind == ENTITY_KIND_INSTANCE,
                WikibaseEntityMapping.run_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.wikibase_id = qid
        if local_key:
            existing.local_key = local_key
        if label:
            existing.label = label
    else:
        db.add(
            WikibaseEntityMapping(
                ontology_uri=key,
                entity_kind=ENTITY_KIND_INSTANCE,
                wikibase_id=qid,
                run_id=None,
                local_key=local_key,
                label=label or local_key or key,
            )
        )
    await db.commit()


def _first_statement_value(item: dict[str, Any] | Any, prop: str) -> str | None:
    stmts = item.get("statements") if isinstance(item, dict) else getattr(item, "statements", None)
    for s in stmts or []:
        if isinstance(s, dict):
            pid = s.get("property") or s.get("property_id")
            if pid == prop:
                return str(s.get("value") or s.get("value_id") or "") or None
        else:
            pid = getattr(s, "property", None) or getattr(s, "property_id", None)
            if pid == prop:
                v = getattr(s, "value", None) or getattr(s, "value_id", None)
                return str(v) if v else None
    return None
