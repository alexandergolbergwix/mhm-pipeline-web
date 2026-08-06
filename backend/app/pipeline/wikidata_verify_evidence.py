"""Multi-source evidence packs for Wikidata Studio AI verify (Rule W-124).

The judge must see every durable channel we already hold — MARC, VIAF,
Mazal/NLI, existing Wikidata, and the project HMO Wikibase — plus the
WikiProject Manuscripts skill (injected separately). This module shapes
those channels into a single ``verify_evidence`` dict on each Studio item
before the eval-agent fixture is written.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.pipeline.marc_verify_context import canonical_control_number
from app.pipeline.wikidata_duplicate_probe import (
    duplicate_check_fallback,
    stamp_duplicate_check,
)
from app.pipeline.wikidata_verdict_cache import marc_context_for_wikidata_item

logger = logging.getLogger(__name__)

_QID_RE = re.compile(r"Q\d+")

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
    # A canonical Bible / Talmud citation is a DIFFERENT source from a 650 subject
    # heading. Citing only `subjects` made every canonical-reference P921 read as
    # unsupported: the judge was shown a person heading as the evidence for
    # "main subject = Exodus" (Rule W-162).
    "P921": ("subjects", "canonical_references"),
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
    # Rule W-162 sweep: every remaining PID a projection can emit as a main snak.
    # Declared-but-not-yet-emitted constants are included deliberately — the point
    # is that the NEXT projection to use one already has provenance, instead of
    # shipping `unmapped` the way P3342 and P1891 did for three months.
    "P18": ("digital_access",),                       # image
    "P27": ("place", "provenance"),                   # country of citizenship
    "P361": ("contents", "related_records", "title"),  # part of
    "P527": ("contents", "related_records"),          # has parts
    "P528": ("record_ids", "shelfmark"),              # catalog code
    "P958": ("extent", "notes"),                      # folio / section
    "P972": ("record_ids",),                          # catalog
    "P793": ("provenance", "notes"),                  # significant event
    "P1552": ("material", "notes"),                   # has quality
    "P1566": ("place",),                              # GeoNames ID
    "P3132": ("notes", "colophon_text", "summary"),   # last line / explicit
    "P7535": ("summary", "notes", "contents"),        # scope and content
    "P12095": ("shelfmark", "provenance"),            # fonds
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

# Role-derived person links. These are not MARC-slice claims (the value is a
# person, not a field's text) and not authority-identifier claims — the evidence
# is the *relator role* that produced the edge, so the row cites the contributor
# or authority match whose role maps to this PID.
#
# Rule W-146 added P3342 / P1891 to ROLE_TO_PID and to the emitter, but never
# added a channel row, so all 21 of them shipped as
# `channels: ["unmapped"], supported: false` — a claim the judge is told to treat
# as unsupported, on a public item (Rule W-162).
_PERSON_LINK_CLAIM_PIDS: dict[str, tuple[str, ...]] = {
    "P3342": ("provenance", "contributors", "notes"),
    "P1891": ("contributors", "notes", "colophon_text"),
    "P127": ("provenance", "contributors"),
    "P11603": ("contributors", "colophon_text", "extent", "material"),
    "P11105": ("contributors", "notes"),
    "P88": ("contributors", "notes"),
    "P110": ("contributors",),
    "P655": ("authors", "contributors"),
    "P9046": ("authors", "contributors"),
    "P50": ("authors",),
    "P2093": ("authors", "contributors"),
    "P1028": ("provenance", "contributors"),   # donated by
    "P11811": ("provenance", "contributors"),  # beforehand owned by
    "P11812": ("provenance", "contributors"),  # afterward owned by
    "P1774": ("contributors", "notes"),        # workshop of
    "P1780": ("contributors", "notes"),        # school of
}

# Claims that follow from the entity's own type rather than from a source
# field. They are true by construction, so "no MARC row" is not a defect.
# P5008 is our own WikiProject Manuscripts membership, not a claim about the
# manuscript, so it has no MARC evidence to cite either.
_STRUCTURAL_CLAIM_PIDS = frozenset({"P31", "P3959", "P5008"})

# `supported` was one boolean for two different facts. These name them.
SUPPORT_SUPPORTED = "supported"
SUPPORT_STRUCTURAL = "structural"
#: The channel exists but this record's field is empty — sparsity, not a defect.
SUPPORT_CHANNEL_EMPTY = "channel_empty"
#: No channel table names this PID at all. That is OUR build defect.
SUPPORT_NO_CHANNEL = "no_channel_mapped"

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


def _roles_for_person_link(pid: str) -> set[str]:
    """The MARC/NER relator roles that map to *pid*, casefolded."""
    from converter.wikidata.property_mapping import ROLE_TO_PID  # noqa: PLC0415

    return {
        str(role).strip().casefold()
        for role, mapped in ROLE_TO_PID.items()
        if mapped == pid and str(role).strip()
    }


def _person_link_evidence(pid: str, item: dict[str, Any], marc: dict[str, str]) -> dict[str, Any]:
    """The role rows that produced a person-link claim (Rule W-162).

    Inverts ``ROLE_TO_PID`` so the evidence names the relator the edge came from,
    rather than handing the judge a whole MARC field and hoping. Reads the same
    ``marc_authority_matches`` / contributor rows the linker itself used.
    """
    roles = _roles_for_person_link(pid)
    if not roles:
        return {}

    def _matches(row: Any) -> bool:
        if not isinstance(row, dict):
            return False
        role = str(row.get("role") or "").strip().casefold()
        return bool(role) and any(role == r or r in role for r in roles)

    rows: list[dict[str, Any]] = []
    for key in ("authority_evidence", "marc_authority_matches", "entities"):
        for row in item.get(key) or []:
            if _matches(row):
                rows.append({
                    "name": row.get("name") or row.get("entity_text") or row.get("text"),
                    "role": row.get("role"),
                    "source": key,
                })

    # The MARC context keeps contributors as one flattened blob, so the matching
    # rows are read out of it rather than re-parsed.
    for name in ("contributors", "provenance"):
        text = str(marc.get(name) or "")
        if not text:
            continue
        for chunk in text.split(" | "):
            lowered = chunk.casefold()
            if any(role in lowered for role in roles):
                rows.append({"marc_field": name, "row": chunk[:200]})

    if not rows:
        return {}
    return {"person_link": _compact(rows[:3]), "roles": ", ".join(sorted(roles))}


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

        if pid in _PERSON_LINK_CLAIM_PIDS:
            channels.append("authority.person_link")
            link_evidence = _person_link_evidence(pid, item, marc)
            if link_evidence:
                evidence.update(link_evidence)

        # An unmapped PID is labelled as such, never "structural" — that label
        # asserts the claim needs no evidence, which is only true for the PIDs
        # in ``_STRUCTURAL_CLAIM_PIDS`` (Rule W-138).
        if structural:
            support_status = SUPPORT_STRUCTURAL
        elif evidence:
            support_status = SUPPORT_SUPPORTED
        elif channels:
            support_status = SUPPORT_CHANNEL_EMPTY
        else:
            support_status = SUPPORT_NO_CHANNEL

        row: dict[str, Any] = {
            "channels": channels or (["structural"] if structural else ["unmapped"]),
            "evidence": evidence,
            # `supported` was one boolean for two facts — "no channel table row
            # exists for this PID" (our bug) and "the channel exists but this
            # record's field is empty" (data sparsity). Kept for compatibility;
            # `support_status` is the one to read (Rule W-162).
            "supported": bool(evidence) or structural,
            "support_status": support_status,
        }
        if structural:
            row["structural"] = True
            row["note"] = (
                "follows from the entity type / catalog record identity — no "
                "separate source field is expected"
            )
        elif support_status == SUPPORT_NO_CHANNEL:
            row["note"] = (
                "BUILD DEFECT: no channel table names this PID, so the judge "
                "cannot trace the claim to any source. See "
                "unmapped_projectable_pids()"
            )
        elif support_status == SUPPORT_CHANNEL_EMPTY:
            row["note"] = (
                "the channel exists but this record's field is empty — catalogue "
                "sparsity, not a projection defect"
            )
        sources[pid] = row
    return sources


def claim_channel_table_pids() -> frozenset[str]:
    """Every PID some channel table can explain."""
    return frozenset(
        set(CLAIM_SOURCE_SLICES)
        | set(_AUTHORITY_CLAIM_PIDS)
        | set(_PERSON_LINK_CLAIM_PIDS)
        | set(_BRIDGE_CLAIM_PIDS)
        | set(_HMO_GATED_IDENTIFIER_PIDS)
        | set(_WORK_LINK_CLAIM_PIDS)
        | set(_STRUCTURAL_CLAIM_PIDS),
    )


# PIDs the projections emit as bare literals rather than via a `P_*` constant.
_EXTRA_PROJECTED_PIDS = frozenset({"P195", "P217", "P31", "P3959"})


def projectable_property_ids() -> frozenset[str]:
    """Every main-snak PID any projection can emit — computed, not hand-listed.

    A hand-maintained list is exactly what went stale: Rule W-146 added two role
    mappings and the matching channel rows were simply forgotten. Derived from the
    ``P_*`` constants and ``ROLE_TO_PID`` so a new mapping shows up here for free.
    """
    import re as _re  # noqa: PLC0415

    from converter.wikidata import property_mapping  # noqa: PLC0415

    pids = set(_EXTRA_PROJECTED_PIDS)
    for name in dir(property_mapping):
        if not name.startswith("P_"):
            continue
        value = getattr(property_mapping, name)
        if isinstance(value, str) and _re.fullmatch(r"P\d+", value):
            pids.add(value)
    pids.update(
        str(v) for v in property_mapping.ROLE_TO_PID.values()
        if isinstance(v, str) and _re.fullmatch(r"P\d+", v)
    )
    return frozenset(pids - property_mapping.QUALIFIER_ONLY_PIDS
                     - property_mapping.REFERENCE_ONLY_PIDS)


def unmapped_projectable_pids() -> frozenset[str]:
    """PIDs a projection can emit that no channel table can explain.

    MUST be empty. A PID in here reaches the judge as
    ``support_status: "no_channel_mapped"`` on a public item (Rule W-162).
    """
    return frozenset(projectable_property_ids() - claim_channel_table_pids())


def build_statement_value_labels(item: dict[str, Any]) -> dict[str, str]:
    """Resolve QID/PID glosses for the judge without any network call.

    A bare ``Q33513`` reads as an unverifiable claim. Three sources, none of them
    on the network: the statement's own ``value_label`` (pre-resolved by
    ``attach_live_value_labels`` from the cached live resolver, so a runtime-
    reconciled place or person is glossed too), the static desktop dictionary, and
    the item's own ``__LOCAL:`` targets (Rule W-80 / W-137). Live WDQS lookups stay
    off the verify path (Rule W-116).
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


def statement_qids_needing_labels(items: list[dict[str, Any]]) -> list[str]:
    """QIDs the static tables cannot gloss — the ones worth a live lookup."""
    from converter.wikidata.property_labels import qid_label  # noqa: PLC0415

    wanted: list[str] = []
    seen: set[str] = set()
    for item in items:
        for statement in item.get("statements") or []:
            if not isinstance(statement, dict):
                continue
            if str(statement.get("value_label") or "").strip():
                continue
            value = str(statement.get("value") or "").strip()
            if not value or value in seen or not _QID_RE.fullmatch(value):
                continue
            seen.add(value)
            if qid_label(value) == value:
                wanted.append(value)
    return wanted


async def attach_live_value_labels(db_or_factory: Any, items: list[dict[str, Any]]) -> int:
    """Stamp ``value_label`` on statements the static tables cannot gloss.

    Rule W-80: the judge and the curator must see the same gloss. A QID reconciled
    at runtime — a KIMA place on P1071, a matched person on P3342 — is in no static
    table, so it rendered bare to the judge while the frontend lazy-fetched a label
    for the very same value. This resolves them through the shared cached resolver
    (process dict → Redis/Postgres → one batched ``wbgetentities``), so a run pays
    for each QID once, ever.

    Never raises and never blocks: a gloss is presentation, and a lookup failure
    leaves the bare QID exactly as before.
    """
    wanted = statement_qids_needing_labels(items)
    if not wanted:
        return 0
    try:
        from app.routers.wikidata_labels import resolve_labels  # noqa: PLC0415

        resolved = await resolve_labels(db_or_factory, wanted)
    except Exception as exc:  # noqa: BLE001 — a gloss must never fail a verify run
        logger.warning("live value-label resolution failed: %s", exc)
        return 0
    if not resolved:
        return 0
    for item in items:
        for statement in item.get("statements") or []:
            if not isinstance(statement, dict):
                continue
            value = str(statement.get("value") or "").strip()
            label = resolved.get(value)
            if label and not str(statement.get("value_label") or "").strip():
                statement["value_label"] = label
    return len(resolved)


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
