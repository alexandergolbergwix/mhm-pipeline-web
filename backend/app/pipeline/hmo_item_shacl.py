"""Map SHACL validation violations to HMO Wikibase item local_ids."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.pipeline.rdf_build import ShaclReport, validate_with_shacl

logger = logging.getLogger(__name__)


async def build_shacl_report_for_items(
    ttl_path: Path,
    entities: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Run SHACL once and bucket violations by ``local_id`` via ``source_uri``.

    A quality signal, not a hard gate: any failure to parse the TTL or run
    pyshacl is logged and degrades to an empty report rather than failing
    the whole item build.
    """
    try:
        report: ShaclReport = await validate_with_shacl(ttl_path)
    except Exception:
        logger.warning("SHACL validation failed for %s; storing empty report", ttl_path, exc_info=True)
        return {}

    uri_to_local: dict[str, str] = {
        str(e.get("source_uri") or ""): str(e.get("local_id") or "")
        for e in entities
        if e.get("source_uri")
    }
    out: dict[str, list[dict[str, Any]]] = {}
    for v in report.violations:
        focus = str(v.focus_node or "")
        local_id = uri_to_local.get(focus)
        if not local_id:
            continue
        out.setdefault(local_id, []).append({
            "focus_node": focus,
            "severity": v.severity,
            "message": v.message,
            "value": v.value,
            "source_shape": v.source_shape,
        })
    if not report.conforms and not out:
        return {"__graph__": [{
            "severity": "Warning",
            "message": "SHACL validation failed but no violations mapped to item local_ids",
            "focus_node": "",
            "value": None,
            "source_shape": "",
        }]}
    return out
