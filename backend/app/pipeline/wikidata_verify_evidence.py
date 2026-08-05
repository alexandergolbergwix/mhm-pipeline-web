"""Multi-source evidence packs for Wikidata Studio AI verify (Rule W-124).

The judge must see every durable channel we already hold — MARC, VIAF,
Mazal/NLI, existing Wikidata, and the project HMO Wikibase — plus the
WikiProject Manuscripts skill (injected separately). This module shapes
those channels into a single ``verify_evidence`` dict on each Studio item
before the eval-agent fixture is written.
"""

from __future__ import annotations

from typing import Any

from app.pipeline.marc_verify_context import canonical_control_number
from app.pipeline.wikidata_duplicate_probe import (
    duplicate_check_fallback,
    stamp_duplicate_check,
)
from app.pipeline.wikidata_verdict_cache import marc_context_for_wikidata_item

_WIKIBASE_HOST_HINTS = (
    "mhm-hmo.wikibase.cloud",
    "wikibase.cloud",
)
_WIKIDATA_HOST_HINTS = (
    "www.wikidata.org",
    "wikidata.org",
)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _authority_rows(item: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _as_list(item.get("authority_evidence")):
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _kind_of(row: dict[str, Any]) -> str:
    return str(row.get("kind") or row.get("source") or "").strip().lower()


def _partition_authority(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    viaf: list[dict[str, Any]] = []
    mazal: list[dict[str, Any]] = []
    wikidata: list[dict[str, Any]] = []
    kima: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for row in rows:
        kind = _kind_of(row)
        if "viaf" in kind:
            viaf.append(row)
        elif "mazal" in kind or kind in {"nli", "j9u"}:
            mazal.append(row)
        elif "wikidata" in kind or kind in {"wd", "qid"}:
            wikidata.append(row)
        elif "kima" in kind:
            kima.append(row)
        else:
            other.append(row)
    return {
        "viaf": viaf,
        "mazal": mazal,
        "wikidata": wikidata,
        "kima": kima,
        "other": other,
    }


def _statement_identifier_rows(item: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    viaf: list[dict[str, str]] = []
    mazal: list[dict[str, str]] = []
    for stmt in _as_list(item.get("statements")):
        if not isinstance(stmt, dict):
            continue
        prop = str(stmt.get("property") or stmt.get("property_id") or "").upper()
        value = str(stmt.get("value") or stmt.get("value_id") or "").strip()
        if not value:
            continue
        if prop == "P214":
            viaf.append({"property": "P214", "value": value})
        elif prop == "P8189":
            mazal.append({"property": "P8189", "value": value})
    return {"viaf_from_statements": viaf, "mazal_from_statements": mazal}


def _bridge_statements(item: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for stmt in _as_list(item.get("statements")):
        if not isinstance(stmt, dict):
            continue
        prop = str(stmt.get("property") or stmt.get("property_id") or "").upper()
        if prop not in {"P2888", "P973"}:
            continue
        value = str(stmt.get("value") or "").strip()
        if not value:
            continue
        host = "hmo_wikibase" if any(h in value for h in _WIKIBASE_HOST_HINTS) else (
            "wikidata" if any(h in value for h in _WIKIDATA_HOST_HINTS) else "other"
        )
        out.append({"property": prop, "value": value, "host": host})
    return out


def _hmo_wikibase_page_url(qid: str) -> str:
    q = str(qid or "").strip()
    if not q:
        return ""
    if not q.upper().startswith("Q"):
        q = f"Q{q}"
    return f"https://mhm-hmo.wikibase.cloud/wiki/Item:{q}"


# Which MARC slice name backs each projected claim. The judge must be able to
# check a claim against *its own* source field instead of scanning a blob —
# absent this map, evidenced claims (dates, extent, shelfmark, rights) read as
# unsupported (Rule W-137).
CLAIM_SOURCE_SLICES: dict[str, tuple[str, ...]] = {
    "P31": ("title", "genres", "carrier"),
    "P50": ("authors",),
    "P127": ("provenance",),
    "P136": ("genres",),
    "P186": ("material",),
    "P195": ("shelfmark", "provenance", "contributors"),
    "P217": ("shelfmark",),
    "P276": ("place", "shelfmark"),
    "P282": ("languages", "material"),
    "P407": ("languages",),
    "P571": ("dates",),
    "P921": ("subjects",),
    "P953": ("digital_access",),
    "P1071": ("place",),
    "P1104": ("extent",),
    "P1476": ("title",),
    "P1574": ("contents", "title", "related_records"),
    "P1680": ("title",),
    "P1684": ("notes", "colophon_text", "summary"),
    "P1922": ("notes", "colophon_text", "summary"),   # first line / incipit
    "P655": ("authors", "contributors"),              # translator
    "P9046": ("authors", "contributors"),             # commentary by
    "P5816": ("notes", "material"),                   # condition
    "P2048": ("extent",),
    "P2049": ("extent",),
    "P2093": ("authors", "contributors"),
    "P2635": ("extent",),
    "P3959": ("record_ids",),
    "P6108": ("digital_access",),
    "P6216": ("rights",),
    "P7153": ("place", "provenance"),
    "P9302": ("material", "languages"),
    "P11603": ("extent", "material"),
}

# Claims whose support is NOT MARC. Without a provenance row of their own the
# judge reported them unsupported on every person and bridge item — 246 items
# carried a P2888 with no row, 122 a P214 (Rule W-138).
_AUTHORITY_CLAIM_PIDS = {
    "P214": "viaf",
    "P8189": "mazal",
    "P244": "authority_other",
    "P227": "authority_other",
    "P213": "authority_other",
    "P268": "authority_other",
    "P1559": "authority_names",
    "P106": "authority_role",
    "P569": "authority_dates",
    "P570": "authority_dates",
    "P1412": "authority_names",
}
_BRIDGE_CLAIM_PIDS = frozenset({"P2888", "P973"})
# Identifier claims whose authority row lives on the HMO Wikibase item rather
# than in this run's approved-match rows.
_HMO_GATED_IDENTIFIER_PIDS = frozenset({"P214", "P8189", "P244", "P227", "P213", "P268"})
_WORK_LINK_CLAIM_PIDS = frozenset({"P1574", "P629", "P747"})

# Claims that follow from the entity's own type rather than from a source
# field. They are true by construction, so "no MARC row" is not a defect.
_STRUCTURAL_CLAIM_PIDS = frozenset({"P31", "P3959"})

_MAX_CLAIM_EVIDENCE_CHARS = 400


_MAIN_TITLE_SOURCE_FIELDS = frozenset({"245", "100/245", "marc_title_author", "marc_245_title"})


def _work_candidate_source_fields(item: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for row in item.get("work_candidate_evidence") or []:
        if isinstance(row, dict):
            for key in ("source_field", "reason"):
                value = str(row.get(key) or "").strip()
                if value:
                    out.add(value)
    return out


def _marc_slices_for(pid: str, item: dict[str, Any]) -> tuple[str, ...]:
    """MARC slices that back ``pid`` **for this entity type**.

    A work's own title is only evidenced by MARC 245 when the work *is* the
    record's main title. For a 505/500/RELATED-derived work, citing `marc.title`
    hands the judge the manuscript's title as "evidence" for a different
    title — it contradicts the claim instead of supporting it, which failed or
    downgraded every 505-derived work (Rule W-138 follow-up).
    """
    slices = CLAIM_SOURCE_SLICES.get(pid, ())
    if pid != "P1476" or str(item.get("entity_type") or "") != "work":
        return slices
    if _work_candidate_source_fields(item) & _MAIN_TITLE_SOURCE_FIELDS:
        return slices
    return tuple(name for name in slices if name != "title") + ("contents", "notes")


def _statement_property_ids(item: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for statement in item.get("statements") or []:
        if not isinstance(statement, dict):
            continue
        pid = str(statement.get("property_id") or statement.get("property") or "").strip()
        if pid and pid not in out:
            out.append(pid)
    return out


def _compact(value: Any) -> str:
    import json as _json  # noqa: PLC0415

    if isinstance(value, str):
        text = value
    else:
        text = _json.dumps(value, ensure_ascii=False)
    return text.strip()[:_MAX_CLAIM_EVIDENCE_CHARS]


def _authority_channel_evidence(item: dict[str, Any]) -> dict[str, list[Any]]:
    """Group the item's accepted authority rows by the channel they support."""
    buckets: dict[str, list[Any]] = {}
    for row in item.get("authority_evidence") or []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or row.get("source") or "").casefold()
        if row.get("viaf_uri") or row.get("viaf_id") or "viaf" in kind:
            buckets.setdefault("viaf", []).append(row)
        if row.get("mazal_id") or "mazal" in kind or kind in {"nli", "j9u"}:
            buckets.setdefault("mazal", []).append(row)
        if row.get("birth_year") or row.get("death_year"):
            buckets.setdefault("authority_dates", []).append(row)
        if row.get("role") or row.get("occupation"):
            buckets.setdefault("authority_role", []).append(row)
        if row.get("preferred_name_heb") or row.get("preferred_name_lat"):
            buckets.setdefault("authority_names", []).append(row)
        buckets.setdefault("authority_other", []).append(row)
    return buckets


def build_claim_sources(
    item: dict[str, Any],
    marc: dict[str, str],
    record_ids: list[str],
) -> dict[str, Any]:
    """Per-claim provenance across every channel the judge is shown.

    MARC-backed claims cite their source field; authority, HMO-bridge and
    work-link claims cite the channel that supports them; type-derived claims
    are marked structural. A claim with no row at all reads as unsupported, so
    every PID we emit must resolve to one of these (Rule W-137 / W-138).
    """
    sources: dict[str, Any] = {}
    authority = _authority_channel_evidence(item)
    evidence_pack_targets = item.get("local_reference_targets") or {}
    work_evidence = item.get("work_candidate_evidence") or []

    for pid in _statement_property_ids(item):
        evidence: dict[str, Any] = {}
        channels: list[str] = []
        structural = pid in _STRUCTURAL_CLAIM_PIDS

        for name in _marc_slices_for(pid, item):
            channels.append(f"marc.{name}")
            if name == "record_ids":
                if record_ids:
                    evidence[name] = ", ".join(record_ids)
                continue
            text = str(marc.get(name) or "").strip()
            if text:
                evidence[name] = text[:_MAX_CLAIM_EVIDENCE_CHARS]

        channel = _AUTHORITY_CLAIM_PIDS.get(pid)
        if channel:
            channels.append(f"authority.{channel}")
            rows = authority.get(channel) or []
            if rows:
                evidence[channel] = _compact(rows[:2])
            elif pid in _HMO_GATED_IDENTIFIER_PIDS and item.get("hmo_wikibase_id"):
                # The identifier reached us on the live HMO Wikibase item, whose
                # authority rows were validated before that item was created
                # (Rule W-95). Citing the wiki item is the truthful provenance;
                # without it a P214 read as unsupported on 122 items.
                channels.append("hmo_wikibase")
                evidence["hmo_wikibase_identifier"] = _compact({
                    "hmo_wikibase_id": item.get("hmo_wikibase_id"),
                    "value": next(
                        (
                            statement.get("value")
                            for statement in item.get("statements") or []
                            if isinstance(statement, dict)
                            and str(
                                statement.get("property_id")
                                or statement.get("property") or "",
                            ) == pid
                        ),
                        None,
                    ),
                    "gated_at": "HMO item creation (authority validation)",
                })

        if pid in _BRIDGE_CLAIM_PIDS:
            channels.append("hmo_wikibase")
            qid = str(item.get("hmo_wikibase_id") or "").strip()
            source_uri = str(item.get("source_uri") or "").strip()
            if qid or source_uri:
                evidence["hmo_wikibase"] = _compact(
                    {"hmo_wikibase_id": qid or None, "source_uri": source_uri or None},
                )

        is_work_title = pid == "P1476" and str(item.get("entity_type") or "") == "work"
        if pid in _WORK_LINK_CLAIM_PIDS or is_work_title:
            channels.append("work_candidate_evidence")
            if work_evidence:
                evidence["work_candidate_evidence"] = _compact(work_evidence[:2])
            if evidence_pack_targets and pid in _WORK_LINK_CLAIM_PIDS:
                channels.append("local_reference_targets")
                evidence["local_reference_targets"] = _compact(
                    sorted(evidence_pack_targets)[:5],
                )

        # An unmapped PID is labelled as such, never "structural" — that label
        # asserts the claim needs no evidence, which is only true for the PIDs
        # in ``_STRUCTURAL_CLAIM_PIDS`` (Rule W-138).
        row: dict[str, Any] = {
            "channels": channels or (["structural"] if structural else ["unmapped"]),
            "evidence": evidence,
            "supported": bool(evidence) or structural,
        }
        if structural:
            row["structural"] = True
            row["note"] = (
                "follows from the entity type / catalog record identity — no "
                "separate source field is expected"
            )
        sources[pid] = row
    return sources


def build_statement_value_labels(item: dict[str, Any]) -> dict[str, str]:
    """Resolve QID/PID glosses for the judge without any network call.

    A bare ``Q33513`` reads as an unverifiable claim; the static desktop
    dictionary plus each item's own ``__LOCAL:`` targets cover everything we
    project (Rule W-137). Live WDQS lookups stay off the verify path
    (Rule W-116).
    """
    from converter.wikidata.property_labels import property_label, qid_label  # noqa: PLC0415

    out: dict[str, str] = {}
    targets = item.get("local_reference_targets")
    local_labels: dict[str, str] = {}
    if isinstance(targets, dict):
        for target_id, target in targets.items():
            labels = target.get("labels") if isinstance(target, dict) else None
            if isinstance(labels, dict):
                text = str(labels.get("en") or labels.get("he") or "").strip()
                if text:
                    local_labels[str(target_id)] = text

    for statement in item.get("statements") or []:
        if not isinstance(statement, dict):
            continue
        pid = str(statement.get("property_id") or statement.get("property") or "").strip()
        if pid:
            label = property_label(pid)
            if label and label != pid:
                out[pid] = label
        value = str(statement.get("value") or "").strip()
        if not value or value in out:
            continue
        if value.startswith("__LOCAL:"):
            target_label = local_labels.get(value.removeprefix("__LOCAL:"))
            if target_label:
                out[value] = target_label
            continue
        if value.upper().startswith("Q"):
            label = str(statement.get("value_label") or "").strip() or qid_label(value)
            if label and label != value:
                out[value] = label
    return out


def build_verify_evidence_pack(
    item: dict[str, Any],
    marc_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Shape every available evidence channel for one Studio item."""
    marc = marc_context_for_wikidata_item(item, marc_records)
    authority = _partition_authority(_authority_rows(item))
    from_stmts = _statement_identifier_rows(item)
    hmo_qid = str(item.get("hmo_wikibase_id") or "").strip()
    existing_qid = str(item.get("existing_qid") or "").strip()
    source_uri = str(item.get("source_uri") or "").strip()
    live = item.get("wikidata_live")
    record_ids = [
        canonical_control_number(cn)
        for cn in (
            item.get("record_ids")
            or item.get("records")
            or item.get("control_numbers")
            or []
        )
        if canonical_control_number(cn)
    ]

    return {
        "record_ids": record_ids,
        "marc": marc,
        "marc_present": bool(marc),
        "claim_sources": build_claim_sources(item, marc, record_ids),
        "value_labels": build_statement_value_labels(item),
        "viaf": {
            "authority_rows": authority["viaf"],
            "from_statements": from_stmts["viaf_from_statements"],
        },
        "mazal": {
            "authority_rows": authority["mazal"],
            "from_statements": from_stmts["mazal_from_statements"],
        },
        "kima": {"authority_rows": authority["kima"]},
        "wikidata_existing": {
            "existing_qid": existing_qid or None,
            "wikidata_uri": (
                f"https://www.wikidata.org/wiki/{existing_qid}"
                if existing_qid else None
            ),
            "authority_rows": authority["wikidata"],
            "live": live if isinstance(live, dict) else None,
            # Live duplicate check for CREATE candidates (Rule W-139). A
            # `candidates_found` status means an item with this identifier is
            # already on Wikidata; `unavailable`/`skipped` means we do NOT know.
            # `stamp_duplicate_check` re-publishes this after the pack is built,
            # so the fallback only survives when no probe ran at all (Rule W-159).
            "duplicate_check": item.get("_wikidata_existence") or duplicate_check_fallback(),
        },
        "hmo_wikibase": {
            "hmo_wikibase_id": hmo_qid or None,
            "source_uri": source_uri or None,
            "page_url": _hmo_wikibase_page_url(hmo_qid) or None,
            "projection_source": item.get("projection_source"),
            "bridge_statements": _bridge_statements(item),
        },
        "authority_other": authority["other"],
        # Span-grounded LLM proposals (Rule W-140). These are review
        # CANDIDATES, not claims — each one quotes the MARC span it came from
        # and nothing here has been projected into `statements`.
        "llm_proposals": item.get("_llm_proposals") or {
            "status": "not_run",
            "proposals": [],
            "note": "LLM extraction did not run for this item",
        },
        "work_candidate_evidence": item.get("work_candidate_evidence") or {},
        "local_reference_targets": item.get("local_reference_targets") or {},
        "wpm_data_model_url": (
            "https://www.wikidata.org/wiki/Wikidata:WikiProject_Manuscripts/Data_Model"
        ),
    }


def enrich_items_with_verify_evidence(
    items: list[dict[str, Any]],
    marc_records: list[dict[str, Any]],
) -> None:
    """Attach ``verify_evidence`` + ``_marc_context`` on every item in place."""
    for item in items:
        pack = build_verify_evidence_pack(item, marc_records)
        item["verify_evidence"] = pack
        item["_marc_context"] = pack.get("marc") or {}
        # Rebuilding the pack would otherwise silently discard a probe answer
        # stamped earlier, which is how the export lost all 343 of them.
        stamp_duplicate_check(item)
