"""Check source semantics and fresh identity before a public item is created."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from converter.wikidata.item_models import WikidataItem

if TYPE_CHECKING:
    from converter.wikidata.reconciler import WikidataReconciler


PUBLIC_ENTITY_TYPES = frozenset({"manuscript", "person", "work"})


def creation_errors(item: WikidataItem) -> list[str]:
    from app.pipeline.wikidata_export_quality_gate import (
        FindingSeverity,
        wikidata_export_quality_findings,
    )

    errors = [
        finding.render()
        for finding in wikidata_export_quality_findings([item])
        if finding.severity == FindingSeverity.HARD
    ]
    if item.entity_type not in PUBLIC_ENTITY_TYPES:
        errors.append("CREATE_ENTITY_TYPE: the public entity type is not supported.")
    active = {
        statement.property_id
        for statement in item.statements
        if statement.rank != "deprecated" and statement.value
    }
    if "P31" not in active:
        errors.append("CREATE_INSTANCE_OF: a new item needs an active instance-of claim.")
    if not str(item.descriptions.get("en") or "").strip():
        errors.append("CREATE_DESCRIPTION: a new item needs an English description.")
    if item.entity_type == "manuscript" and "P3959" not in active:
        errors.append("CREATE_SOURCE_ID: a new manuscript needs its exact NLI item ID.")
    if item.entity_type == "manuscript" and "P217" not in active:
        errors.append("CREATE_SHELFMARK: a new manuscript needs a proven current shelfmark.")
    for pid in ("P214", "P8189", "P244", "P227", "P213", "P268", "P3959"):
        values = {
            str(statement.value).strip()
            for statement in item.statements
            if statement.property_id == pid and statement.rank != "deprecated" and statement.value
        }
        if len(values) > 1:
            errors.append(f"CREATE_IDENTITY_CONFLICT: {pid} has several active identities.")
    return errors


def reconcile_creation_identity(
    reconciler: WikidataReconciler,
    item: WikidataItem,
) -> tuple[str | None, str]:
    from app.pipeline.wikidata_upload import _reconcile_for_upload

    properties = {
        statement.property_id
        for statement in item.statements
        if statement.rank != "deprecated" and statement.value
    }
    probeable = (
        item.entity_type == "person"
        and bool(properties & {"P214", "P8189", "P244", "P227", "P213"})
        or item.entity_type == "manuscript"
        and bool(properties & {"P3959", "P217"})
        or item.entity_type == "work"
        and (bool(item.labels.get("he") or item.labels.get("en")) or "P2888" in properties)
    )
    if not probeable:
        raise ValueError("No supported duplicate check exists for this CREATE")
    active_item = replace(
        item,
        statements=[statement for statement in item.statements if statement.rank != "deprecated"],
    )
    qid, method = _reconcile_for_upload(reconciler, active_item, item.entity_type)
    if not qid and method == "source_scoped_work":
        raise ValueError("The source-scoped work did not receive a duplicate check")
    if not qid and item.entity_type == "work" and not properties.intersection({"P50", "P2093"}):
        raise ValueError("A work without author evidence cannot prove duplicate absence")
    return qid, method
