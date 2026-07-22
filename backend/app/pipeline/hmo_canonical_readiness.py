from __future__ import annotations
from typing import Any

def check(items: list[dict[str, Any]]) -> dict[str, Any]:
    local_ids = [str(item.get("local_id") or "") for item in items]
    source_uris = [str(item.get("source_uri") or "") for item in items]
    qids = [str(item.get("wikibase_id") or "") for item in items]
    missing = [item.get("local_id") for item in items if not item.get("canonical_live")]
    return {"items": len(items), "missing_canonical_live": len(missing), "missing_examples": missing[:20], "duplicate_local_ids": len(local_ids)-len(set(local_ids)), "duplicate_source_uris": len(source_uris)-len(set(source_uris)), "duplicate_wikibase_ids": len([q for q in qids if q])-len({q for q in qids if q}), "ready": bool(items) and not missing and len(local_ids)==len(set(local_ids)) and len(source_uris)==len(set(source_uris))}
