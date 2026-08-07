"""Safe preparation of Wikidata candidates from canonical HMO entities."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
import hashlib
import json
import logging
import re

from app.pipeline.hmo_canonical import CanonicalHmoEntity, assert_canonical_entities
from app.pipeline.marc_verify_context import (
    canonical_control_number,
    primary_control_number_for,
)
from converter.authority.evidence import normalize_authority_id, normalize_viaf_id, normalize_wikidata_qid
from converter.wikidata.hmo_wikidata_pq_mapper import (
    HMO_CLASS_TO_WIKIDATA_QID,
    map_hmo_claim_to_wikidata,
    map_local_name_to_wikidata_pid,
    ontology_local_name,
)
from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.projection_coverage import STRATEGY_BY_LOCAL_NAME, ProjectionStrategy
from converter.wikidata.property_mapping import (
    P_DESCRIBED_AT_URL,
    P_EXACT_MATCH,
    P_INSTANCE_OF,
    P_NLI_CATALOG_ID,
    Q_HUMAN,
    Q_MANUSCRIPT,
    Q_WRITTEN_WORK,
    is_browseable_hmo_wikibase_url,
    resolve_hmo_bridge_url,
)

logger = logging.getLogger(__name__)

_QID = re.compile(r"^Q[1-9][0-9]*$")
_MAX_DESCRIPTION_LENGTH = 250

_BOILERPLATE_DESCRIPTION = (
    re.compile(r"^Offline HMO Wikibase draft for ", re.I),
    re.compile(r"^Linked to \d+ manuscripts", re.I),
    re.compile(r" linked to manuscript \d+\.?$", re.I),
    re.compile(r" in the Hebrew manuscripts corpus\.?$", re.I),
    re.compile(r"^(F\d+_|E\d+_)[A-Za-z_ ]+\.?"),
    re.compile(r"^[A-Za-z][A-Za-z0-9_ ]+ linked to manuscript \d+\.?$"),
    re.compile(r"^Literary work '.*' in manuscript \d+\.?$"),
    re.compile(r"^Author linked to manuscript\.?$"),
    re.compile(r"^person associated with Hebrew manuscripts\.?$", re.I),
    re.compile(r"^Work preserved in a Hebrew manuscript\.?$", re.I),
)

_PERSON_IDENTIFIER_PIDS = frozenset({"P214", "P8189", "P244", "P227", "P213", "P268"})

PUBLIC_WIKIDATA_ENTITY_TYPES = frozenset({"manuscript", "person", "work"})

_HARD_REJECT_AUTHORITY_FLAGS = frozenset({
    "placeholder_name",
    "non_person_heading",
    "date_conflict",
    "biographical_inconsistency",
    "modern_person",
})
# "We cannot confirm this identity for this heading" — not "this is not a person".
# The item survives on its MARC attestation, but its dates and its authority-derived
# label do not (Rule W-166). Deliberately NOT added to the hard-reject set, which
# drives `_drop_conflicted_person_items`: a soft flag must never remove the item.
_SOFT_REJECT_AUTHORITY_FLAGS = frozenset({"wikidata_crosscheck_fail"})
_DATE_SUPPRESSING_AUTHORITY_FLAGS = (
    _HARD_REJECT_AUTHORITY_FLAGS | _SOFT_REJECT_AUTHORITY_FLAGS
)
# P921 values too broad to say anything about a manuscript. Q9190 used to be in
# here because `QID_LABELS` glossed it as "Jews" — it is actually **Exodus**, so
# this filter was silently dropping a perfectly specific biblical subject while
# believing it dropped a generic one. Verified live 2026-08-05 (Rule W-26).
_BROAD_MAIN_SUBJECT_QIDS = frozenset({"Q7325"})  # Jewish people
_PERSON_BIOGRAPHY_PIDS = frozenset({"P569", "P570"})
# Soft-reject / heading-mismatch: these identity claims must not ship when the
# authority row is unconfirmed (Rule W-170). Leaving P8189 on CREATE while
# blocking W-168 adoption is exactly the corner the judge fails under W-139.
_UNCONFIRMED_IDENTITY_PIDS = (
    _PERSON_BIOGRAPHY_PIDS | _PERSON_IDENTIFIER_PIDS | frozenset({"P1559"})
)


def identity_control_number(entity: CanonicalHmoEntity) -> str:
    """The control number this entity is about (Rule W-137).

    HMO propagates linked CNs onto derived nodes (Rule W-48); identity —
    labels, shelfmark, description, the legacy MARC join — must use the CN in
    the entity's own source URI / local id.
    """
    return primary_control_number_for(
        entity.control_numbers,
        entity.source_uri,
        entity.local_id,
    )


def identity_records_for(entity: CanonicalHmoEntity, wd_type: str) -> list[str]:
    """Manuscripts carry only their own record; persons/works span records."""
    if wd_type == "manuscript":
        cn = identity_control_number(entity)
        return [cn] if cn else []
    return list(entity.control_numbers)


def is_public_wikidata_studio_item(
    item: Mapping[str, Any],
    *,
    source: str = "canonical",
) -> bool:
    """True when a cached Studio row is a public WPM item (manuscript/person/work)."""
    entity_type = str(item.get("entity_type") or "")
    if entity_type in PUBLIC_WIKIDATA_ENTITY_TYPES:
        return True
    if entity_type:
        return False
    return source == "legacy"


def filter_public_wikidata_items(
    items: Iterable[Mapping[str, Any]],
    *,
    source: str = "canonical",
) -> list[dict[str, Any]]:
    """Drop HMO ontology rows that must never reach verify/export/UI."""
    from app.pipeline.wikidata_verdict_cache import record_ids_for_wikidata_item  # noqa: PLC0415

    filtered: list[dict[str, Any]] = []
    for raw in items:
        if not is_public_wikidata_studio_item(raw, source=source):
            continue
        item = dict(raw)
        record_ids = record_ids_for_wikidata_item(item)
        if record_ids:
            item["record_ids"] = record_ids
            if not item.get("records"):
                item["records"] = list(record_ids)
        filtered.append(item)
    return filtered


def studio_cache_has_non_public_items(
    items: Iterable[Mapping[str, Any]] | None,
    *,
    source: str = "canonical",
) -> bool:
    """Detect pre-W-117 canonical caches that still store HMO class entity_type values."""
    if source != "canonical" or not items:
        return False
    return any(
        not is_public_wikidata_studio_item(item, source=source)
        for item in items
    )

# HMO classes whose summarized claims roll onto work items (not manuscripts).
_WORK_ROLLUP_ENTITY_TYPES = frozenset({"F27_Work_Creation", "Work_Creation"})

# Backward-compatible aliases — prefer ``hmo_wikidata_pq_mapper``.
_HMO_PROPERTY_TO_WIKIDATA_PID = {
    name: pid for name, pid in (
        (k, map_local_name_to_wikidata_pid(k)) for k in (
            "viaf_id", "external_identifier_nli", "external_uri_nli", "authority_id",
            "mazal_id", "shelfmark", "inventory_number", "has_language", "has_script_type",
            "has_material", "has_number_of_folios", "has_title", "has_genre",
            "has_main_subject", "has_iiif_manifest_url", "has_described_at_url",
            "has_full_work_url", "has_copyright_status", "has_condition",
            "has_date_of_creation", "has_location_of_creation", "has_occupation",
            "has_date_of_birth", "has_date_of_death", "has_native_name",
        )
    ) if pid
}
_INSTANCE_QIDS = dict(HMO_CLASS_TO_WIKIDATA_QID)


@dataclass
class CanonicalStudioContext:
    """Optional MARC + authority context for production-grade descriptions."""

    marc_by_cn: dict[str, dict[str, Any]] = field(default_factory=dict)
    matches_by_viaf: dict[str, dict[str, Any]] = field(default_factory=dict)
    matches_by_mazal: dict[str, dict[str, Any]] = field(default_factory=dict)
    matches_by_qid: dict[str, dict[str, Any]] = field(default_factory=dict)
    matches_by_name: dict[str, dict[str, Any]] = field(default_factory=dict)


def canonical_studio_context(
    marc_records: Iterable[Mapping[str, Any]] | None = None,
    approved_matches: Iterable[Mapping[str, Any]] | None = None,
) -> CanonicalStudioContext:
    from app.pipeline.marc_ingest import prepare_record_for_pipeline  # noqa: PLC0415
    from converter.rdf.rdf_helpers import sanitize_work_title  # noqa: PLC0415

    marc_by_cn: dict[str, dict[str, Any]] = {}
    for raw in marc_records or []:
        record = prepare_record_for_pipeline(dict(raw))
        cn = canonical_control_number(
            str(record.get("_control_number") or record.get("control_number") or ""),
        )
        if cn:
            marc_by_cn[cn] = record

    matches_by_viaf: dict[str, dict[str, Any]] = {}
    matches_by_mazal: dict[str, dict[str, Any]] = {}
    matches_by_qid: dict[str, dict[str, Any]] = {}
    matches_by_name: dict[str, dict[str, Any]] = {}
    # The work projection resolves a work's author off the record's own
    # ``marc_authority_matches``. The canonical context indexed the matches for
    # its own lookups but never stamped them back onto the records, so
    # ``_find_work_author_match`` had nothing to read and every work degraded to
    # a P2093 author *name string* — 43 of 105, against 1 P50 (Rule W-146).
    # Must be the *desktop* shape the builder reads (`name`, `viaf_uri`, quote-
    # stripped `role`), not the raw DB row — a raw row has no `name` at all, so
    # every person failed the Wikidata:Notability check for want of an identifier.
    from app.pipeline.wikidata_studio import (  # noqa: PLC0415
        _approved_match_to_desktop_shape,
    )

    matches_by_cn: dict[str, list[dict[str, Any]]] = {}
    for raw in approved_matches or []:
        cn = canonical_control_number(str(raw.get("control_number") or ""))
        if cn:
            matches_by_cn.setdefault(cn, []).append(
                _approved_match_to_desktop_shape(dict(raw)),
            )
    for cn, record in marc_by_cn.items():
        record["marc_authority_matches"] = matches_by_cn.get(cn, [])

    for raw in approved_matches or []:
        match = dict(raw)
        viaf = normalize_viaf_id(match.get("viaf_id"))
        if viaf:
            matches_by_viaf[viaf] = match
        mazal = normalize_authority_id(match.get("mazal_id"))
        if mazal:
            matches_by_mazal[mazal] = match
        qid = normalize_wikidata_qid(match.get("wikidata_qid"))
        if qid:
            matches_by_qid[qid] = match
        for key in ("entity_text", "matched_name"):
            name = str(match.get(key) or "").strip()
            if not name:
                continue
            matches_by_name[name.casefold()] = match
            sanitized = sanitize_work_title(name).casefold().strip(" .,;:/-\"'")
            if sanitized:
                matches_by_name[sanitized] = match
    return CanonicalStudioContext(
        marc_by_cn=marc_by_cn,
        matches_by_viaf=matches_by_viaf,
        matches_by_mazal=matches_by_mazal,
        matches_by_qid=matches_by_qid,
        matches_by_name=matches_by_name,
    )


def wikidata_candidates_from_hmo(
    entities: Iterable[CanonicalHmoEntity],
) -> list[dict[str, Any]]:
    """Return projection records grounded exclusively in HMO state."""
    all_entities = list(entities)
    materialized = uploadable_entities_from_hmo(all_entities)
    assert_canonical_entities(materialized)
    entities_by_cn = _index_entities_by_control_number(all_entities)
    return [
        {
            "local_id": entity.local_id,
            "source_uri": entity.source_uri,
            "hmo_wikibase_id": entity.wikibase_id,
            "labels": dict(entity.labels),
            "descriptions": dict(entity.descriptions),
            "aliases": dict(entity.aliases),
            "claims": native_wikidata_claims(
                entity,
                rollup_sources=_rollup_sources_for(
                    entity,
                    _wikidata_entity_type(entity) or "",
                    all_entities,
                    entities_by_cn,
                ),
            ),
            "authority_evidence": [
                evidence for evidence in entity.authority_evidence
                if evidence.get("accepted") is True
            ],
            "projection_source": "hmo_wikibase",
            "source_fingerprint": entity.source_fingerprint,
            "entity_type": _wikidata_entity_type(entity),
            "control_numbers": list(entity.control_numbers),
        }
        for entity in materialized
    ]


def canonical_wikidata_fingerprint(
    entities: Iterable[CanonicalHmoEntity],
    *,
    enrichment_fingerprint: str | None = None,
) -> str:
    candidates = wikidata_candidates_from_hmo(entities)
    payload = json.dumps(candidates, ensure_ascii=False, sort_keys=True, default=str)
    # v9: MARC/authority enrichment merge (Rule W-125) participates in the salt.
    # v10 — Rule W-137 (manuscript identity scoping, one catalog id per
    # manuscript, generated descriptions, deduped claims).
    # v11 — Rule W-138 (unwrapped MARC values, one work title, evidence-gated
    # person dates, resolved local references).
    salt = "hmo-wikidata-v12:"  # Rules W-161 … W-166
    if enrichment_fingerprint:
        salt = f"{salt}enrich={enrichment_fingerprint}:"
    return hashlib.sha256((salt + payload).encode()).hexdigest()


def uploadable_entities_from_hmo(
    entities: Iterable[CanonicalHmoEntity],
) -> list[CanonicalHmoEntity]:
    """Keep only HMO classes that map to public Wikidata items."""
    uploadable: list[CanonicalHmoEntity] = []
    for entity in entities:
        wd_type = _wikidata_entity_type(entity)
        if not wd_type:
            continue
        if wd_type == "manuscript" and not entity.control_numbers:
            continue
        if wd_type == "work" and not (entity.labels.get("he") or entity.labels.get("en")):
            continue
        if wd_type == "person" and not _person_has_upload_identifier(entity):
            continue
        uploadable.append(entity)
    return uploadable


def native_wikidata_claims(
    entity: CanonicalHmoEntity,
    *,
    ontology_uri_by_project_pid: Mapping[str, str] | None = None,
    rollup_sources: Iterable[CanonicalHmoEntity] | None = None,
) -> list[dict[str, str]]:
    native: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    wd_type = _wikidata_entity_type(entity) or ""

    def _append_mapped(claim_dict: dict[str, str]) -> None:
        key = (claim_dict["property"], claim_dict["value"])
        if key in seen:
            return
        seen.add(key)
        native.append(claim_dict)

    for claim in entity.claims:
        mapped = map_hmo_claim_to_wikidata(
            claim,
            entity_type=wd_type,
            ontology_uri_by_project_pid=ontology_uri_by_project_pid,
            project_item_qid=entity.wikibase_id,
        )
        if mapped is None:
            continue
        property_id = mapped.property_id
        raw = mapped.value
        if property_id == "P8189" and not str(raw).startswith("9870"):
            if wd_type == "manuscript" and str(raw).startswith("990"):
                property_id = P_NLI_CATALOG_ID
            else:
                continue
        _append_mapped({"property": property_id, "value": str(raw)})

    for source in rollup_sources or ():
        allowed_pids = _allowed_rollup_pids(source)
        if not allowed_pids:
            continue
        for claim in source.claims:
            mapped = map_hmo_claim_to_wikidata(
                claim,
                entity_type=wd_type,
                ontology_uri_by_project_pid=ontology_uri_by_project_pid,
                project_item_qid=source.wikibase_id,
            )
            if mapped is None or mapped.property_id not in allowed_pids:
                continue
            property_id = mapped.property_id
            raw = mapped.value
            if property_id == "P8189" and not str(raw).startswith("9870"):
                if wd_type == "manuscript" and str(raw).startswith("990"):
                    property_id = P_NLI_CATALOG_ID
                else:
                    continue
            _append_mapped({"property": property_id, "value": str(raw)})

    # One catalog id per manuscript — its own. Emitting a P3959 per linked CN
    # put 4–5 NNL ids on every item and claimed other manuscripts' records
    # (Rule W-137).
    if wd_type == "manuscript":
        cn = identity_control_number(entity)
        key = (P_NLI_CATALOG_ID, cn)
        if cn and key not in seen:
            seen.add(key)
            native.append({"property": P_NLI_CATALOG_ID, "value": cn})

    for evidence in entity.authority_evidence:
        if evidence.get("accepted") is not True:
            continue
        kind = str(evidence.get("kind") or "").lower()
        identifier = _evidence_identifier(evidence)
        if not identifier:
            continue
        if kind == "viaf":
            key = ("P214", identifier)
            if key not in seen:
                seen.add(key)
                native.append({"property": "P214", "value": identifier})
        elif kind == "mazal" and identifier.startswith("9870"):
            key = ("P8189", identifier)
            if key not in seen:
                seen.add(key)
                native.append({"property": "P8189", "value": identifier})

    instance_qid = _default_instance_qid(wd_type)
    if instance_qid:
        key = (P_INSTANCE_OF, instance_qid)
        if key not in seen:
            seen.add(key)
            native.append({"property": P_INSTANCE_OF, "value": instance_qid})

    return native


def native_items_from_hmo(
    entities: Iterable[CanonicalHmoEntity],
    *,
    context: CanonicalStudioContext | None = None,
) -> list[WikidataItem]:
    """Adapt live HMO snapshots to the guarded Wikidata upload model."""
    all_entities = list(entities)
    materialized = uploadable_entities_from_hmo(all_entities)
    assert_canonical_entities(materialized)
    entities_by_cn = _index_entities_by_control_number(all_entities)
    items: list[WikidataItem] = []
    for entity in materialized:
        wd_type = _wikidata_entity_type(entity) or ""
        assert wd_type in PUBLIC_WIKIDATA_ENTITY_TYPES
        existing_qid = _accepted_wikidata_qid(entity)
        if wd_type == "work" and not existing_qid:
            from converter.wikidata.property_mapping import known_work_qid_for_title  # noqa: PLC0415

            for title in _work_title_candidates(entity):
                existing_qid = known_work_qid_for_title(title)
                if existing_qid:
                    break
        rollup_sources = _rollup_sources_for(entity, wd_type, all_entities, entities_by_cn)
        statements = [
            WikidataStatement(
                property_id=claim["property"],
                value=_claim_value_for(claim["property"], claim["value"]),
                value_type="wikibase-item" if _QID.fullmatch(claim["value"]) else "string",
            )
            for claim in native_wikidata_claims(entity, rollup_sources=rollup_sources)
        ]
        _append_manuscript_bridge_statements(entity, wd_type, statements)
        labels, aliases = _wikidata_labels_and_aliases(entity, wd_type, context=context)
        work_evidence = (
            _work_candidate_evidence_for(entity, context=context)
            if wd_type == "work"
            else []
        )
        # A candidate accepted *because* a known Wikidata work exists must carry
        # that QID. Without this, `הגדה של פסח` was accepted via
        # `known_wikidata_work` and then minted as a local CREATE — a duplicate
        # of an item that already exists on Wikidata (Rule W-114 / W-138).
        if wd_type == "work" and not existing_qid:
            existing_qid = _known_qid_from_work_evidence(work_evidence)
        # Fail closed: never emit a CREATE work without accepted source evidence
        # (Rule W-68 / WORK_WITHOUT_SOURCE_EVIDENCE). Existing QIDs may UPDATE.
        if (
            wd_type == "work"
            and not existing_qid
            and not any(row.get("accepted") is True for row in work_evidence)
        ):
            continue
        items.append(WikidataItem(
            labels=labels,
            descriptions=_upload_descriptions(entity, wd_type, context=context),
            aliases=aliases,
            statements=statements,
            existing_qid=existing_qid,
            entity_type=wd_type,
            local_id=entity.local_id,
            records=identity_records_for(entity, wd_type),
            authority_evidence=[
                evidence for evidence in entity.authority_evidence
                if evidence.get("accepted") is True
            ],
            work_candidate_evidence=work_evidence,
        ))
    return items


def build_canonical_studio_result(
    entities: Iterable[CanonicalHmoEntity],
    *,
    overrides: dict[str, dict[str, Any]] | None = None,
    return_native: bool = False,
    reconcile: bool = True,
    context: CanonicalStudioContext | None = None,
    legacy_native_items: list[Any] | None = None,
) -> dict[str, Any]:
    """Build serialised Wikidata Studio items from durable HMO snapshots.

    When ``legacy_native_items`` is provided (Rule W-125), merge the rich
    MARC/authority projection onto canonical items while keeping HMO
    ``local_id`` / bridge / ``existing_qid`` identity.
    """
    from app.pipeline import wikidata_studio  # noqa: PLC0415
    from app.pipeline.wikidata_canonical_enrichment import (  # noqa: PLC0415
        is_publishable_person_item,
        merge_legacy_into_canonical,
    )
    from app.pipeline.wikidata_upload import _reconcile_sync  # noqa: PLC0415
    from converter.wikidata.item_validator import validate_item  # noqa: PLC0415
    from converter.wikidata.quickstatements import QuickStatementsExporter  # noqa: PLC0415

    materialized = list(entities)
    uploadable = uploadable_entities_from_hmo(materialized)
    rollup_stats = _rollup_summary_stats(materialized, uploadable)
    native_items = native_items_from_hmo(materialized, context=context)
    if legacy_native_items:
        native_items = merge_legacy_into_canonical(native_items, list(legacy_native_items))
    entities_by_local_id = {entity.local_id: entity for entity in materialized}

    if overrides:
        for item in native_items:
            ov = overrides.get(item.local_id)
            if ov:
                wikidata_studio._apply_override(item, ov)

    if reconcile and native_items:
        for item, outcome in zip(native_items, _reconcile_sync(native_items), strict=True):
            if outcome.existing_qid and not item.existing_qid:
                item.existing_qid = outcome.existing_qid

    dropped_person_ids = [
        item.local_id
        for item in native_items
        if (
            str(item.entity_type or "").strip().lower() == "person"
            and not is_publishable_person_item(item)
        )
    ]
    if dropped_person_ids:
        logger.warning(
            "Dropping identifierless canonical Wikidata persons before validation: %s",
            ", ".join(dropped_person_ids),
        )
        native_items = [
            item
            for item in native_items
            if (
                str(item.entity_type or "").strip().lower() != "person"
                or is_publishable_person_item(item)
            )
        ]

    conflicted_person_ids = _drop_conflicted_person_items(native_items, context)
    if conflicted_person_ids:
        logger.warning(
            "Dropping authority-conflicted canonical Wikidata persons: %s",
            ", ".join(conflicted_person_ids),
        )
        native_items = [
            item for item in native_items
            if item.local_id not in set(conflicted_person_ids)
        ]

    unconfirmed_ids = _suppress_unconfirmed_person_identity(native_items, context)
    if unconfirmed_ids:
        logger.warning(
            "Suppressing identity claims on persons whose authority is unconfirmed "
            "(wikidata_crosscheck_fail / heading_mismatch / preferred↔label): %s",
            ", ".join(unconfirmed_ids),
        )
    # Soft-reject may have stripped the only publishable IDs — drop those persons
    # before local-ref resolve (Rule W-154 / W-170).
    after_strip_dropped = [
        item.local_id
        for item in native_items
        if (
            str(item.entity_type or "").strip().lower() == "person"
            and not is_publishable_person_item(item)
        )
    ]
    if after_strip_dropped:
        logger.warning(
            "Dropping persons left identifierless after unconfirmed-identity strip: %s",
            ", ".join(after_strip_dropped),
        )
        native_items = [
            item
            for item in native_items
            if (
                str(item.entity_type or "").strip().lower() != "person"
                or is_publishable_person_item(item)
            )
        ]

    _sanitize_canonical_claims(native_items, context)
    _align_person_p1559_to_hebrew_label(native_items)

    # Apply overrides before resolving placeholders. A curator statement edit
    # may itself add a __LOCAL target, and the resolver must see the final item
    # set after identifierless/conflicted persons have been removed.
    from app.pipeline.wikidata_local_refs import resolve_local_references  # noqa: PLC0415

    local_ref_stats = resolve_local_references(native_items)

    per_item_issues: list[list[dict[str, Any]]] = []
    for item in native_items:
        issue_dicts: list[dict[str, Any]] = []
        for issue in validate_item(item):
            sev = issue.severity if hasattr(issue, "severity") else str(issue)
            code = issue.code if hasattr(issue, "code") else ""
            msg = issue.message if hasattr(issue, "message") else str(issue)
            log_fn = logger.error if sev == "error" else logger.warning
            log_fn("validate_item [%s] %s: %s", sev.upper(), code, msg)
            issue_dicts.append({"code": code, "severity": sev, "message": msg})
        per_item_issues.append(issue_dicts)

    serialised = [wikidata_studio._serialise_item(item) for item in native_items]
    for item_dict, item, issues in zip(serialised, native_items, per_item_issues, strict=True):
        public_type = str(item_dict.get("entity_type") or "")
        if public_type not in PUBLIC_WIKIDATA_ENTITY_TYPES:
            raise ValueError(
                f"Wikidata Studio item {item.local_id} has non-public entity_type: {public_type}",
            )
        entity = entities_by_local_id.get(item.local_id)
        item_dict.update({
            "source_uri": entity.source_uri if entity else item_dict.get("source_uri"),
            "hmo_wikibase_id": entity.wikibase_id if entity else item_dict.get("hmo_wikibase_id"),
            "projection_source": (
                "hmo_wikibase+marc" if legacy_native_items and entity
                else ("marc" if legacy_native_items and not entity else "hmo_wikibase")
            ),
            "source_fingerprint": entity.source_fingerprint if entity else None,
            "validation_issues": issues,
        })

    exporter = QuickStatementsExporter()
    return {
        "items": serialised,
        "native_items": native_items if return_native else None,
        "quickstatements": exporter.export(native_items),
        "summary": {
            "total_items": len(serialised),
            "manuscripts": sum(1 for item in native_items if item.entity_type == "manuscript"),
            "persons": sum(1 for item in native_items if item.entity_type == "person"),
            "works": sum(1 for item in native_items if item.entity_type == "work"),
            "statements": sum(len(item.statements) for item in native_items),
            "skipped_entities": max(0, len(materialized) - len(native_items)),
            "rolled_up_entities": rollup_stats["rolled_up_entities"],
            "summarized_hmo_nodes": rollup_stats["summarized_hmo_nodes"],
            "legacy_enriched": bool(legacy_native_items),
            "local_references_degraded": local_ref_stats["degraded"],
            "local_references_dropped": local_ref_stats["dropped"],
            "conflicted_persons_dropped": len(conflicted_person_ids),
            "unconfirmed_person_identity_suppressed": len(unconfirmed_ids),
            "identifierless_after_soft_reject": len(after_strip_dropped),
        },
    }


def _person_identifier_keys(item: WikidataItem) -> set[str]:
    keys: set[str] = set()
    for statement in item.statements or []:
        pid = str(statement.property_id or "")
        value = str(statement.value or "").strip()
        if not value:
            continue
        if pid == "P214":
            keys.add(f"viaf:{normalize_viaf_id(value) or value.rstrip('/').rsplit('/', 1)[-1]}")
        elif pid == "P8189":
            keys.add(f"mazal:{normalize_authority_id(value) or value}")
        elif pid in _PERSON_IDENTIFIER_PIDS and pid not in {"P214", "P8189"}:
            keys.add(f"id:{pid}:{value}")
    if item.existing_qid:
        keys.add(f"qid:{normalize_wikidata_qid(item.existing_qid) or item.existing_qid}")
    for row in item.authority_evidence or []:
        if not isinstance(row, dict):
            continue
        viaf = normalize_viaf_id(row.get("viaf_uri") or row.get("viaf_id"))
        mazal = normalize_authority_id(row.get("mazal_id") or row.get("identifier"))
        qid = normalize_wikidata_qid(row.get("wikidata_qid") or row.get("identifier"))
        if viaf:
            keys.add(f"viaf:{viaf}")
        if mazal:
            keys.add(f"mazal:{mazal}")
        if qid:
            keys.add(f"qid:{qid}")
    return keys


def _authority_match_keys(match: Mapping[str, Any]) -> set[str]:
    payload = match.get("payload") or {}
    if not isinstance(payload, Mapping):
        payload = {}
    keys: set[str] = set()
    viaf = normalize_viaf_id(match.get("viaf_id") or payload.get("viaf_uri"))
    mazal = normalize_authority_id(match.get("mazal_id"))
    qid = normalize_wikidata_qid(match.get("wikidata_qid") or payload.get("wikidata_qid"))
    if viaf:
        keys.add(f"viaf:{viaf}")
    if mazal:
        keys.add(f"mazal:{mazal}")
    if qid:
        keys.add(f"qid:{qid}")
    return keys


def _authority_match_conflicts(
    match: Mapping[str, Any],
    record: Mapping[str, Any],
) -> bool:
    payload = match.get("payload") or {}
    if not isinstance(payload, Mapping):
        payload = {}
    flags = set(match.get("guard_flags") or []) | set(payload.get("guard_flags") or [])
    if flags & _HARD_REJECT_AUTHORITY_FLAGS:
        return True
    from app.pipeline.marc_date_sources import manuscript_production_year  # noqa: PLC0415
    from converter.authority.stage3_guards import (  # noqa: PLC0415
        evaluate_date_conflict,
        evaluate_modern_person_conflict,
    )

    ms_year = manuscript_production_year(dict(record))
    birth = match.get("birth_year") or payload.get("birth_year")
    death = match.get("death_year") or payload.get("death_year")
    try:
        birth_year = int(birth) if birth is not None else None
    except (TypeError, ValueError):
        birth_year = None
    try:
        death_year = int(death) if death is not None else None
    except (TypeError, ValueError):
        death_year = None
    if evaluate_modern_person_conflict(ms_year, birth_year):
        return True
    return bool(
        evaluate_date_conflict(
            role=str(match.get("role") or ""),
            ms_year=ms_year,
            person_birth_year=birth_year,
            person_death_year=death_year,
        )
    )


def _item_label_texts(item: WikidataItem) -> list[str]:
    return [
        str(value).strip()
        for value in (item.labels or {}).values()
        if str(value or "").strip()
    ]


def _preferred_names_from_evidence(item: WikidataItem) -> list[str]:
    prefs: list[str] = []
    for row in item.authority_evidence or []:
        if not isinstance(row, Mapping):
            continue
        for key in ("preferred_name_heb", "preferred_name_lat", "matched_name"):
            text = str(row.get(key) or "").strip()
            if text and text not in prefs:
                prefs.append(text)
    return prefs


def _preferred_names_from_context(
    item: WikidataItem,
    context: CanonicalStudioContext | None,
) -> list[str]:
    """Recover Mazal preferred names via emitted P8189 when evidence was slimmed."""
    if context is None:
        return []
    prefs: list[str] = []
    for statement in item.statements or []:
        if str(statement.property_id or "") != "P8189":
            continue
        mazal = normalize_authority_id(statement.value)
        if not mazal:
            continue
        match = context.matches_by_mazal.get(mazal) or {}
        for key in ("preferred_name_heb", "preferred_name_lat", "matched_name", "entity_text"):
            text = str(match.get(key) or "").strip()
            if text and text not in prefs:
                prefs.append(text)
    return prefs


def _same_family_different_person(label: str, preferred: str) -> bool:
    """True when surnames match but given names do not (Sara Molho vs Shabtai Molho).

    Broader preferred↔label mismatch is common on *passing* rows (family vs
    personal headings, toponymic bynames). Stripping those is a regression.
    Same-family / different-given is the conflation that must never ship.
    """
    from converter.authority.heading_fidelity import (  # noqa: PLC0415
        _given_and_surname,
        heading_matches,
    )
    from converter.authority.wikidata_crosscheck import hebrew_label_matches  # noqa: PLC0415

    if not label or not preferred or heading_matches(label, preferred):
        return False
    label_given, label_surname = _given_and_surname(label)
    pref_given, pref_surname = _given_and_surname(preferred)
    if not (label_given and pref_given and label_surname and pref_surname):
        return False
    if not hebrew_label_matches(label_surname, [pref_surname], max_distance=1):
        return False
    return not hebrew_label_matches(label_given, [pref_given], max_distance=1)


def _same_family_heading_conflict(item: WikidataItem, prefs: list[str]) -> bool:
    """True when a preferred form is the same family but a different person."""
    labels = _item_label_texts(item)
    if not prefs or not labels:
        return False
    from converter.authority.heading_fidelity import heading_matches  # noqa: PLC0415

    if any(heading_matches(label, preferred) for label in labels for preferred in prefs):
        return False
    return any(
        _same_family_different_person(label, preferred)
        for label in labels
        for preferred in prefs
    )


def _suppress_unconfirmed_person_identity(
    items: list[WikidataItem],
    context: CanonicalStudioContext | None = None,
) -> list[str]:
    """Strip identity claims from an unconfirmed authority row (Rule W-170).

    ``wikidata_crosscheck_fail`` means no Wikidata label for that cluster is
    within two edits of the MARC name — we cannot confirm the identity for this
    heading. ``heading_mismatch`` means Mazal preferred_name names someone else.
    Canonical HMO can still emit P8189 without that flag; recover only the
    same-family / different-given conflation (Sara≠Shabtai) from evidence or the
    Mazal row behind P8189 — not every preferred↔label disagreement (those ship
    on many full-passing rows). Strip identifiers and P1559; clear a bad
    ``existing_qid``; the later W-154 gate drops the person when nothing
    publishable remains. Dates go with the same pass (Rule W-166).
    """
    from converter.authority.heading_fidelity import heading_mismatch_reason  # noqa: PLC0415

    suppressed: list[str] = []
    for item in items:
        if str(item.entity_type or "").strip().lower() != "person":
            continue
        flags: set[str] = set()
        for row in item.authority_evidence or []:
            if isinstance(row, Mapping):
                flags |= {str(f) for f in (row.get("guard_flags") or [])}
        prefs = _preferred_names_from_evidence(item) or _preferred_names_from_context(
            item, context,
        )
        family_conflict = _same_family_heading_conflict(item, prefs)
        if family_conflict and not getattr(item, "heading_mismatch", None):
            labels = _item_label_texts(item)
            item.heading_mismatch = {
                "marc": labels[0] if labels else "",
                "authority": prefs[0] if prefs else "",
                "reason": heading_mismatch_reason(
                    labels[0] if labels else "",
                    prefs[0] if prefs else "",
                ),
            }
        untrusted = bool(flags & _SOFT_REJECT_AUTHORITY_FLAGS) or bool(
            getattr(item, "heading_mismatch", None)
        ) or family_conflict
        if not untrusted:
            continue
        if item.existing_qid:
            item.existing_qid = None
        kept = [
            statement for statement in item.statements or []
            if str(statement.property_id or "") not in _UNCONFIRMED_IDENTITY_PIDS
        ]
        if len(kept) != len(item.statements or []):
            item.statements = kept
            suppressed.append(item.local_id)
        elif item.local_id not in suppressed and (
            family_conflict or getattr(item, "heading_mismatch", None)
        ):
            suppressed.append(item.local_id)
        for lang, text in list((item.descriptions or {}).items()):
            cleaned = re.sub(r"\s*\([^)]*\d{3,4}[^)]*\)", "", str(text or "")).strip()
            if cleaned != text:
                item.descriptions[lang] = cleaned
    return suppressed


def _suppress_unconfirmed_person_dates(
    items: list[WikidataItem],
    context: CanonicalStudioContext | None = None,
) -> list[str]:
    """Compat alias — identity strip now covers dates too (Rule W-170)."""
    return _suppress_unconfirmed_person_identity(items, context)


def _align_person_p1559_to_hebrew_label(items: list[WikidataItem]) -> None:
    """After merge, P1559 MUST equal ``labels["he"]`` (Rule W-170)."""
    for item in items:
        if str(item.entity_type or "").strip().lower() != "person":
            continue
        he_label = str((item.labels or {}).get("he") or "").strip()
        without = [
            statement for statement in item.statements or []
            if str(statement.property_id or "") != "P1559"
        ]
        if he_label:
            without.append(
                WikidataStatement(
                    property_id="P1559",
                    value=he_label,
                    value_type="monolingualtext",
                    language="he",
                )
            )
        item.statements = without


def _drop_conflicted_person_items(
    items: list[WikidataItem],
    context: CanonicalStudioContext | None,
) -> list[str]:
    if context is None:
        return []
    dropped: list[str] = []
    for item in items:
        if str(item.entity_type or "").strip().lower() != "person":
            continue
        if not any(
            str(statement.property_id or "") in _PERSON_BIOGRAPHY_PIDS
            for statement in item.statements or []
        ) and not item.authority_evidence:
            continue
        identifiers = _person_identifier_keys(item)
        matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for raw_cn in item.records or []:
            cn = canonical_control_number(raw_cn)
            record = context.marc_by_cn.get(cn)
            if not record:
                continue
            for raw_match in record.get("marc_authority_matches") or []:
                if not isinstance(raw_match, dict):
                    continue
                if identifiers & _authority_match_keys(raw_match):
                    matched.append((record, raw_match))
        if matched and all(_authority_match_conflicts(match, record) for record, match in matched):
            dropped.append(str(item.local_id or ""))
    return [local_id for local_id in dropped if local_id]


def _sanitize_canonical_claims(
    items: list[WikidataItem],
    context: CanonicalStudioContext | None,
) -> None:
    """Remove known broad subjects and unsupported canonical holder claims."""
    from converter.wikidata.manuscript_projection import (  # noqa: PLC0415
        _current_holder_names,
        _current_holder_qid,
    )

    for item in items:
        kept: list[WikidataStatement] = []
        record: dict[str, Any] | None = None
        if context is not None and str(item.entity_type or "").lower() == "manuscript":
            cn = next(
                (
                    canonical_control_number(raw_cn)
                    for raw_cn in item.records or []
                    if canonical_control_number(raw_cn)
                ),
                "",
            )
            record = context.marc_by_cn.get(cn)
        holder_qid = None
        if record is not None:
            holder_qid = _current_holder_qid(record, _current_holder_names(record))
        for statement in item.statements or []:
            pid = str(statement.property_id or "")
            value = str(statement.value or "")
            if pid == "P921" and value in _BROAD_MAIN_SUBJECT_QIDS:
                continue
            if pid == "P195" and record is not None and value != holder_qid:
                continue
            kept.append(statement)
        item.statements = kept


def quickstatements_from_canonical(entities: Iterable[CanonicalHmoEntity]) -> str:
    from converter.wikidata.quickstatements import QuickStatementsExporter  # noqa: PLC0415

    return QuickStatementsExporter().export(native_items_from_hmo(entities))


def _wikidata_entity_type(entity: CanonicalHmoEntity) -> str:
    strategy = STRATEGY_BY_LOCAL_NAME.get(entity.entity_type or "")
    if strategy and strategy.projection_status == "direct_wikidata_item":
        return strategy.item_entity_type
    return ""


def _projection_strategy(entity: CanonicalHmoEntity) -> ProjectionStrategy | None:
    return STRATEGY_BY_LOCAL_NAME.get(entity.entity_type or "")


def _index_entities_by_control_number(
    entities: Iterable[CanonicalHmoEntity],
) -> dict[str, list[CanonicalHmoEntity]]:
    by_cn: dict[str, list[CanonicalHmoEntity]] = {}
    for entity in entities:
        for raw_cn in entity.control_numbers:
            cn = canonical_control_number(str(raw_cn))
            if not cn:
                continue
            bucket = by_cn.setdefault(cn, [])
            if entity not in bucket:
                bucket.append(entity)
    return by_cn


def _allowed_rollup_pids(entity: CanonicalHmoEntity) -> frozenset[str]:
    strategy = _projection_strategy(entity)
    if strategy is None or strategy.projection_status != "summarized_in_wikidata":
        return frozenset()
    return frozenset(strategy.wikidata_properties)


def _rollup_sources_for(
    target: CanonicalHmoEntity,
    target_wd_type: str,
    all_entities: list[CanonicalHmoEntity],
    entities_by_cn: dict[str, list[CanonicalHmoEntity]],
) -> list[CanonicalHmoEntity]:
    """Collect summarized HMO nodes whose claims fold onto a public Wikidata item."""
    if target_wd_type not in PUBLIC_WIKIDATA_ENTITY_TYPES:
        return []
    target_cns = {
        canonical_control_number(str(cn))
        for cn in target.control_numbers
        if canonical_control_number(str(cn))
    }
    if not target_cns:
        return []

    uploadable_ids = {
        entity.local_id
        for entity in all_entities
        if _wikidata_entity_type(entity)
    }
    related: list[CanonicalHmoEntity] = []
    seen_local_ids: set[str] = set()

    for cn in target_cns:
        for entity in entities_by_cn.get(cn, ()):
            if entity.local_id == target.local_id:
                continue
            if entity.local_id in seen_local_ids:
                continue
            if entity.local_id in uploadable_ids:
                continue
            strategy = _projection_strategy(entity)
            if strategy is None or strategy.projection_status != "summarized_in_wikidata":
                continue
            if target_wd_type == "work" and entity.entity_type not in _WORK_ROLLUP_ENTITY_TYPES:
                continue
            if target_wd_type == "manuscript" and entity.entity_type in _WORK_ROLLUP_ENTITY_TYPES:
                continue
            seen_local_ids.add(entity.local_id)
            related.append(entity)
    return related


def _rollup_summary_stats(
    materialized: list[CanonicalHmoEntity],
    uploadable: list[CanonicalHmoEntity],
) -> dict[str, int]:
    entities_by_cn = _index_entities_by_control_number(materialized)
    rolled_up_ids: set[str] = set()
    summarized_nodes = 0
    for entity in materialized:
        strategy = _projection_strategy(entity)
        if strategy and strategy.projection_status == "summarized_in_wikidata":
            summarized_nodes += 1
    for entity in uploadable:
        wd_type = _wikidata_entity_type(entity) or ""
        for source in _rollup_sources_for(entity, wd_type, materialized, entities_by_cn):
            rolled_up_ids.add(source.local_id)
    return {
        "rolled_up_entities": len(rolled_up_ids),
        "summarized_hmo_nodes": summarized_nodes,
    }


def _default_instance_qid(entity_type: str) -> str:
    if entity_type == "manuscript":
        return Q_MANUSCRIPT
    if entity_type == "work":
        return Q_WRITTEN_WORK
    if entity_type == "person":
        return Q_HUMAN
    return ""


def _property_local_name(property_uri: str) -> str:
    return ontology_local_name(property_uri)


def _claim_scalar_value(claim: dict[str, Any]) -> str:
    value = claim.get("value")
    if isinstance(value, dict):
        return str(value.get("text") or value.get("value") or "").strip()
    return str(value or claim.get("wikidata_value") or claim.get("target_qid") or "").strip()


def _evidence_identifier(evidence: dict[str, Any]) -> str:
    for key in ("identifier", "value", "wikidata_qid"):
        text = str(evidence.get(key) or "").strip()
        if text:
            return text
    return ""


def _accepted_wikidata_qid(entity: CanonicalHmoEntity) -> str | None:
    accepted_qids = {
        qid for qid in (
            normalize_wikidata_qid(_evidence_identifier(evidence))
            for evidence in entity.authority_evidence
            if evidence.get("accepted") is True
            and str(evidence.get("kind") or "").lower() == "wikidata"
        )
        if qid
    }
    return next(iter(accepted_qids)) if len(accepted_qids) == 1 else None


def _person_has_upload_identifier(entity: CanonicalHmoEntity) -> bool:
    if _accepted_wikidata_qid(entity):
        return True
    for claim in native_wikidata_claims(entity):
        if claim["property"] in _PERSON_IDENTIFIER_PIDS:
            return True
    for evidence in entity.authority_evidence:
        if evidence.get("accepted") is not True:
            continue
        kind = str(evidence.get("kind") or "").lower()
        identifier = _evidence_identifier(evidence)
        if kind == "viaf" and normalize_viaf_id(identifier):
            return True
        if kind == "mazal" and identifier.startswith("9870"):
            return True
    for claim in entity.claims:
        property_name = _property_local_name(str(claim.get("property_uri") or ""))
        if property_name == "viaf_id" and normalize_viaf_id(_claim_scalar_value(claim)):
            return True
        if property_name in {"external_identifier_nli", "external_uri_nli", "authority_id", "mazal_id"}:
            value = normalize_authority_id(_claim_scalar_value(claim))
            if value and value.startswith("9870"):
                return True
    return False


def _is_boilerplate_description(text: str) -> bool:
    cleaned = str(text or "").strip()
    if not cleaned:
        return True
    return any(pattern.search(cleaned) for pattern in _BOILERPLATE_DESCRIPTION)


_CATALOG_NOTE_DESCRIPTION = (
    re.compile(r"MARC 33[678]"),
    re.compile(r"content type:|media type:|carrier type:", re.I),
    re.compile(r"^\s*(f|ff)\.?\s*\d+[ab]?\s*[:.]", re.I),
    re.compile(r"^\s*(According to|Related material|Formerly|Bound with)\b", re.I),
    re.compile(r"^\s*(דף|דפים|בסוף|בראש|בתוך)\s"),
)

_MAX_DESCRIPTION_PROSE_LENGTH = 180


def _is_catalog_note_description(text: str) -> bool:
    """True when a stored description is really a MARC note (Rule W-137)."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return True
    if len(cleaned) > _MAX_DESCRIPTION_PROSE_LENGTH:
        return True
    return any(pattern.search(cleaned) for pattern in _CATALOG_NOTE_DESCRIPTION)


def _truncate_description(text: str) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= _MAX_DESCRIPTION_LENGTH:
        return cleaned
    cut = cleaned[: _MAX_DESCRIPTION_LENGTH - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


def _upload_labels(entity: CanonicalHmoEntity) -> dict[str, str]:
    """Backward-compatible label fallback (no type/context). Prefer
    ``_wikidata_labels_and_aliases``.
    """
    labels, _aliases = _wikidata_labels_and_aliases(entity, _wikidata_entity_type(entity) or "")
    return labels


def _wikidata_labels_and_aliases(
    entity: CanonicalHmoEntity,
    entity_type: str,
    *,
    context: CanonicalStudioContext | None = None,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Public Wikidata labels: script-correct slots, natural person order, MS shelfmark en."""
    from converter.wikidata.item_builder import (  # noqa: PLC0415
        _has_hebrew_script,
        _holding_institution_name,
        _is_placeholder_title,
        _normalise_label,
        _strip_person_name_qualifiers,
        _to_natural_name_order,
    )

    raw_labels = {
        str(lang): str(value).strip()
        for lang, value in (entity.labels or {}).items()
        if str(value).strip()
    }
    aliases: dict[str, list[str]] = {
        str(lang): [str(v).strip() for v in values if str(v).strip()]
        for lang, values in (entity.aliases or {}).items()
        if isinstance(values, list)
    }

    if entity_type == "manuscript":
        return _manuscript_labels_and_aliases(
            entity,
            raw_labels,
            aliases,
            context=context,
            normalise=_normalise_label,
            has_hebrew=_has_hebrew_script,
            is_placeholder=_is_placeholder_title,
            holding_name=_holding_institution_name,
        )
    if entity_type == "person":
        return _person_labels_and_aliases(
            entity,
            raw_labels,
            aliases,
            context=context,
            normalise=_normalise_label,
            has_hebrew=_has_hebrew_script,
            strip_qualifiers=_strip_person_name_qualifiers,
            to_natural=_to_natural_name_order,
        )
    if entity_type == "work":
        return _work_labels_and_aliases(
            raw_labels,
            aliases,
            normalise=_normalise_label,
            has_hebrew=_has_hebrew_script,
        )

    if raw_labels:
        return _route_labels_by_script(raw_labels, has_hebrew=_has_hebrew_script), aliases
    if entity.control_numbers:
        return {"en": f"NLI manuscript {identity_control_number(entity)}"}, aliases
    return {"en": entity.local_id.replace("QDraft_", "").replace("_", " ")}, aliases


def _route_labels_by_script(
    raw_labels: Mapping[str, str],
    *,
    has_hebrew,
) -> dict[str, str]:
    """Never leave Hebrew-only text in ``en`` or Latin-only text in ``he``."""
    out: dict[str, str] = {}
    for lang, value in raw_labels.items():
        text = str(value).strip()
        if not text:
            continue
        hebrew = has_hebrew(text)
        if lang == "en" and hebrew and not re.search(r"[A-Za-z]", text):
            out.setdefault("he", text)
            continue
        if lang == "he" and not hebrew and re.search(r"[A-Za-z]", text):
            out.setdefault("en", text)
            continue
        out[str(lang)] = text
    return out


def _manuscript_labels_and_aliases(
    entity: CanonicalHmoEntity,
    raw_labels: Mapping[str, str],
    aliases: dict[str, list[str]],
    *,
    context: CanonicalStudioContext | None,
    normalise,
    has_hebrew,
    is_placeholder,
    holding_name,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    labels: dict[str, str] = {}
    record = _marc_record_for_entity(entity, context)
    title = ""
    if record:
        title = str(record.get("title") or "").strip()
    if not title:
        title = str(raw_labels.get("he") or raw_labels.get("en") or "").strip()
    title_clean = normalise(title) if title else ""
    title_has_hebrew = has_hebrew(title_clean)
    placeholder = is_placeholder(title_clean) if title_clean else False

    if title_clean and not placeholder and not title_has_hebrew:
        labels["en"] = title_clean
        aliases.setdefault("en", []).append(title_clean)

    shelfmark = ""
    if record:
        shelfmark = normalise(str(record.get("shelfmark") or ""))
    if not shelfmark:
        shelfmark = _shelfmark_from_claims(entity)
    cn = ""
    if record:
        cn = str(record.get("_control_number") or record.get("control_number") or "").strip()
    if not cn and entity.control_numbers:
        cn = identity_control_number(entity)

    # A manuscript is a physical carrier, so its label is a DESIGNATION — holder
    # plus shelfmark — not the title of a text it happens to contain. 64 of 68
    # manuscripts in run 48ba6c13 carried the MARC 245 in `labels.he`, which the
    # judge flagged as "the work title, not the manuscript" (Rule W-164). The
    # title stays searchable as an alias.
    designation_suffix = shelfmark or cn
    if designation_suffix:
        from converter.wikidata.item_builder import (  # noqa: PLC0415
            manuscript_en_label,
            manuscript_record_label,
        )

        holding = holding_name(record) if record else ""
        labels["en"] = (
            manuscript_en_label(shelfmark, holding) if shelfmark
            else manuscript_record_label(cn, record)
        )
        labels["he"] = _hebrew_manuscript_label(record, designation_suffix)
        if title_clean and title_has_hebrew:
            aliases.setdefault("he", []).append(title_clean)
    elif title_clean and title_has_hebrew and not placeholder:
        # Nothing better exists: no shelfmark and no control number, so the title
        # is the only designation available.
        labels["he"] = title_clean
    elif title_clean:
        aliases.setdefault("he", []).append(title_clean)

    if "en" not in labels and "he" not in labels:
        labels = _route_labels_by_script(raw_labels, has_hebrew=has_hebrew) or {
            "en": entity.local_id.replace("QDraft_", "").replace("_", " "),
        }

    return labels, _dedupe_aliases(aliases, labels)


def _person_labels_and_aliases(
    entity: CanonicalHmoEntity,
    raw_labels: Mapping[str, str],
    aliases: dict[str, list[str]],
    *,
    context: CanonicalStudioContext | None,
    normalise,
    has_hebrew,
    strip_qualifiers,
    to_natural,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    labels: dict[str, str] = {}

    def _place(name: str, *, as_alias: bool = False) -> None:
        cleaned = normalise(_strip_ms_scope_suffix(strip_qualifiers(name)))
        if not cleaned:
            return
        natural = normalise(strip_qualifiers(to_natural(cleaned)))
        lang = "he" if has_hebrew(natural) else "en"
        if as_alias or (lang in labels and labels[lang] != natural):
            if natural and natural not in (aliases.get(lang) or []):
                aliases.setdefault(lang, []).append(natural)
            return
        labels[lang] = natural
        if cleaned != natural and cleaned not in (aliases.get(lang) or []):
            aliases.setdefault(lang, []).append(cleaned)

    primary = str(raw_labels.get("he") or raw_labels.get("en") or "").strip()
    if primary:
        _place(_strip_ms_scope_suffix(primary))

    for lang, value in raw_labels.items():
        text = str(value).strip()
        if text and text != primary:
            _place(_strip_ms_scope_suffix(text), as_alias=lang in labels)

    if context is not None:
        match = _authority_match_for_entity(entity, context)
        if match:
            for key in ("preferred_name_heb", "preferred_name_lat", "matched_name", "entity_text"):
                pref = str(match.get(key) or "").strip()
                if pref:
                    _place(pref, as_alias=True)

    if not labels:
        labels = _route_labels_by_script(raw_labels, has_hebrew=has_hebrew)
    if not labels:
        labels = {"en": entity.local_id.replace("QDraft_", "").replace("_", " ")}
    return labels, _dedupe_aliases(aliases, labels)


def _work_labels_and_aliases(
    raw_labels: Mapping[str, str],
    aliases: dict[str, list[str]],
    *,
    normalise,
    has_hebrew,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    labels: dict[str, str] = {}
    for lang, value in raw_labels.items():
        title = normalise(str(value).strip())
        if not title:
            continue
        if has_hebrew(title):
            labels.setdefault("he", title)
        else:
            labels.setdefault("en", title)
            if title not in (aliases.get("en") or []):
                aliases.setdefault("en", []).append(title)
    if not labels:
        labels = _route_labels_by_script(raw_labels, has_hebrew=has_hebrew)
    return labels, _dedupe_aliases(aliases, labels)


def _dedupe_aliases(
    aliases: Mapping[str, list[str]],
    labels: Mapping[str, str],
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for lang, values in aliases.items():
        seen: set[str] = set()
        label = str(labels.get(lang) or "").strip()
        cleaned: list[str] = []
        for value in values:
            text = str(value).strip()
            if not text or text == label or text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
        if cleaned:
            out[str(lang)] = cleaned
    return out


def _marc_record_for_entity(
    entity: CanonicalHmoEntity,
    context: CanonicalStudioContext | None,
) -> dict[str, Any] | None:
    """The MARC record this entity *is about* — its own, never a linked one.

    Everything downstream of this decides identity (label, shelfmark, holder,
    description). Taking "the first linked record that exists" is what let one
    manuscript's shelfmark label three others (Rule W-137).
    """
    if context is None:
        return None
    cn = identity_control_number(entity)
    record = context.marc_by_cn.get(cn) if cn else None
    return dict(record) if record else None


def _work_identity_record(
    entity: CanonicalHmoEntity,
    context: CanonicalStudioContext | None,
) -> tuple[str, dict[str, Any] | None]:
    """The single record this WORK item is attested from (Rule W-165).

    Manuscripts have had this anchor since Rule W-137; works had none, so the
    label came from the HMO snapshot, the description's "attested in NLI record X"
    clause came from the first control number that happened to index, and
    `work_candidate_evidence` came from an independent third walk. `QDraft_Work_37`
    ended up naming three different records at once.

    Precedence:
      1. ``identity_control_number(entity)`` when it indexes in ``marc_by_cn``;
      2. the UNIQUE control number whose MARC 245 / contents / mentions / related
         works strictly match this work's own label;
      3. ``("", None)`` — fail closed rather than pick one.
    """
    if context is None:
        return "", None

    identity_cn = identity_control_number(entity)
    if identity_cn:
        record = context.marc_by_cn.get(identity_cn)
        if record:
            return identity_cn, dict(record)

    titles = _work_title_candidates(entity)
    if not titles:
        return "", None

    matches: list[tuple[str, dict[str, Any]]] = []
    for raw_cn in entity.control_numbers:
        cn = canonical_control_number(str(raw_cn)) or ""
        record = context.marc_by_cn.get(cn) if cn else None
        if not record:
            continue
        if any(_titles_match(t, cn_title) for t in titles for cn_title in _record_titles(record)):
            matches.append((cn, dict(record)))
    if len(matches) == 1:
        return matches[0]
    return "", None


def _record_titles(record: dict[str, Any]) -> list[str]:
    """Every title a record attests, for anchoring a work to it."""
    from converter.rdf.rdf_helpers import parse_contents_entry  # noqa: PLC0415

    titles: list[str] = []
    main = str(record.get("title") or "").strip()
    if main:
        titles.append(main)
    for content in record.get("contents") or []:
        title = _contents_row_fields(content, parse_contents_entry)[0]
        if title:
            titles.append(title)
    for key in ("work_mentions", "related_works"):
        for row in record.get(key) or []:
            text = (
                str(row.get("title") or row.get("name") or "")
                if isinstance(row, dict) else str(row)
            ).strip()
            if text:
                titles.append(text)
    return titles


def _shelfmark_from_claims(entity: CanonicalHmoEntity) -> str:
    from converter.wikidata.item_builder import _normalise_label  # noqa: PLC0415

    for claim in entity.claims:
        local = ontology_local_name(str(claim.get("property_uri") or ""))
        pid = str(claim.get("property_id") or claim.get("wikidata_property") or "")
        if local == "shelfmark" or pid in {"P217", "inventory_number"}:
            value = str(claim.get("value") or "").strip()
            if value:
                return _normalise_label(value)
    return ""


def _work_candidate_evidence_for(
    entity: CanonicalHmoEntity,
    *,
    context: CanonicalStudioContext | None,
) -> list[dict[str, object]]:
    """Stamp accepted W-68 evidence for a CREATE work, or leave empty to drop."""
    from converter.rdf.rdf_helpers import parse_contents_entry, sanitize_work_title  # noqa: PLC0415
    from converter.wikidata.item_builder import _is_placeholder_title  # noqa: PLC0415
    from converter.wikidata.property_mapping import known_work_qid_for_title  # noqa: PLC0415
    from converter.wikidata.work_candidates import assess_work_candidate  # noqa: PLC0415

    titles = _work_title_candidates(entity)
    if not titles:
        return []

    known_qid = _accepted_wikidata_qid(entity)
    for candidate in titles:
        known_qid = known_qid or known_work_qid_for_title(candidate)

    def _try_assess(
        title: str,
        *,
        source_field: str,
        approved: bool = False,
        known: str | None = None,
        candidate_kind: str = "",
        folio_range: str = "",
        sequence: object = None,
        source_text: str = "",
        source_record_id: str = "",
        source_scope: str = "record",
    ) -> list[dict[str, object]] | None:
        decision = assess_work_candidate(
            title,
            source_field=source_field,
            approved=approved,
            known_qid=known or known_qid,
            candidate_kind=candidate_kind,
            folio_range=folio_range,
            sequence=sequence,
            source_text=source_text or title,
        )
        if decision.accepted:
            evidence = decision.evidence()
            # Always present, even when empty: "no record backs this row" is a
            # stated fact, not a missing key. 45 of 106 rows in the export carried
            # no `source_record_id` at all (Rule W-165).
            evidence["source_record_id"] = source_record_id or ""
            evidence["source_scope"] = source_scope
            return [evidence]
        return None

    if context is not None:
        for title in titles:
            match = _authority_work_match(title, context)
            if match:
                hit = _try_assess(
                    title,
                    source_field="AUTHORITY",
                    approved=True,
                    known=normalize_wikidata_qid(match.get("wikidata_qid")),
                    source_text=str(
                        match.get("entity_text") or match.get("matched_name") or title
                    ),
                    source_record_id=_work_identity_record(entity, context)[0],
                    source_scope="authority",
                )
                if hit:
                    return hit

        # ONE record, not an independent walk of every linked control number.
        # The description cites the anchor, so the evidence must come from the same
        # record or the item names two sources at once (Rule W-165).
        anchor_cn, anchor_record = _work_identity_record(entity, context)
        for raw_cn, record in (
            [(anchor_cn, anchor_record)] if anchor_record is not None else []
        ):
            for content in record.get("contents") or []:
                content_title, folio, sequence, source_text, source_field = (
                    _contents_row_fields(content, parse_contents_entry)
                )
                if not any(_titles_match(t, content_title) for t in titles):
                    continue
                hit = _try_assess(
                    content_title or titles[0],
                    source_field=source_field,
                    folio_range=folio,
                    sequence=sequence,
                    source_text=source_text,
                    source_record_id=canonical_control_number(str(raw_cn)),
                )
                if hit:
                    return hit

            for mention in record.get("work_mentions") or []:
                if isinstance(mention, dict):
                    mention_title = str(
                        mention.get("title") or mention.get("text") or ""
                    ).strip()
                    kind = str(
                        mention.get("kind") or mention.get("candidate_kind") or "named_work"
                    )
                    source_text = str(mention.get("source_text") or mention_title)
                else:
                    mention_title = str(mention).strip()
                    kind = "named_work"
                    source_text = mention_title
                if not any(_titles_match(t, mention_title) for t in titles):
                    continue
                hit = _try_assess(
                    mention_title or titles[0],
                    source_field="500",
                    candidate_kind=kind,
                    source_text=source_text,
                    source_record_id=canonical_control_number(str(raw_cn)),
                )
                if hit:
                    return hit

            for related in record.get("related_works") or []:
                if isinstance(related, dict):
                    related_title = str(
                        related.get("title") or related.get("text") or ""
                    ).strip()
                    related_qid = normalize_wikidata_qid(
                        related.get("wikidata_qid") or related.get("qid")
                    ) or known_work_qid_for_title(related_title)
                    source_text = str(related.get("source_text") or related_title)
                else:
                    related_title = str(related).strip()
                    related_qid = known_work_qid_for_title(related_title)
                    source_text = related_title
                if not any(_titles_match(t, related_title) for t in titles):
                    continue
                hit = _try_assess(
                    related_title or titles[0],
                    source_field="RELATED",
                    known=related_qid,
                    approved=bool(related_qid),
                    source_text=source_text,
                    source_record_id=canonical_control_number(str(raw_cn)),
                )
                if hit:
                    return hit

            # Main MARC 245 work (GraphBuilder always mints F1_Work from 245).
            marc_title = sanitize_work_title(str(record.get("title") or "")).strip()
            if (
                marc_title
                and not _is_placeholder_title(marc_title)
                and any(_titles_match(t, marc_title) for t in titles)
            ):
                has_authors = bool(record.get("authors") or record.get("author"))
                source_field = "100/245" if has_authors else "245"
                hit = _try_assess(
                    marc_title,
                    source_field=source_field,
                    candidate_kind=(
                        "marc_title_author" if has_authors else "marc_245_title"
                    ),
                    source_text=marc_title,
                    source_record_id=canonical_control_number(str(raw_cn)),
                )
                if hit:
                    return hit

    # These two rest on a known QID rather than on a record, so they say so
    # instead of leaving `source_record_id` silently absent (Rule W-165).
    if known_qid:
        hit = _try_assess(
            titles[0], source_field="HMO", known=known_qid, source_scope="hmo_wikibase",
        )
        if hit:
            return hit

    for title in titles:
        mapped = known_work_qid_for_title(title)
        if mapped:
            hit = _try_assess(
                title, source_field="KNOWN_ALIAS", known=mapped,
                source_scope="known_wikidata_work",
            )
            if hit:
                return hit

    return []


def _work_title_candidates(entity: CanonicalHmoEntity) -> list[str]:
    from converter.rdf.rdf_helpers import sanitize_work_title  # noqa: PLC0415

    seen: set[str] = set()
    out: list[str] = []

    def _add(raw: object) -> None:
        text = sanitize_work_title(str(raw or "")).strip()
        if not text:
            return
        for candidate in (text, _strip_ms_scope_suffix(text)):
            key = candidate.casefold()
            if not candidate or key in seen:
                continue
            seen.add(key)
            out.append(candidate)

    for lang in ("he", "en"):
        _add((entity.labels or {}).get(lang))
    for values in (entity.aliases or {}).values():
        if isinstance(values, list):
            for value in values:
                _add(value)
    for claim in entity.claims:
        local = ontology_local_name(str(claim.get("property_uri") or ""))
        if local in {"has_title", "title"}:
            _add(claim.get("value"))
    return out


_TITLE_VALUE_PIDS = frozenset({"P1476", "P1680", "P1932"})


def _claim_value_for(property_id: str, value: str) -> str:
    """Sanitise title-valued claims the same way labels are sanitised.

    `P1476` shipped raw ISBD pairs like `"הגדה של פסח :" "מנהג אשכנז."` while the
    label carried the clean form — the same string, quoted twice, disagreeing
    with itself (Rule W-45 / W-50 hygiene, enforced on the canonical claim path
    at Rule W-138).
    """
    if property_id not in _TITLE_VALUE_PIDS:
        return value
    from converter.rdf.rdf_helpers import sanitize_work_title  # noqa: PLC0415

    cleaned = sanitize_work_title(str(value or ""))
    return cleaned or value


def _known_qid_from_work_evidence(work_evidence: list[dict[str, Any]]) -> str:
    """Resolve the known Wikidata QID that made a work candidate acceptable.

    ``assess_work_candidate`` accepts a candidate with ``reason
    known_wikidata_work`` when its *evidence* title matches a known work, but
    the item's own label may be a longer ISBD form that no longer matches
    exactly (Rule W-78). Recovering the QID from the evidence titles keeps the
    two decisions consistent (Rule W-138 follow-up).
    """
    from converter.rdf.rdf_helpers import sanitize_work_title  # noqa: PLC0415
    from converter.wikidata.property_mapping import known_work_qid_for_title  # noqa: PLC0415

    for row in work_evidence:
        if not isinstance(row, dict) or row.get("accepted") is not True:
            continue
        for key in ("title", "raw_title", "source_text"):
            candidate = str(row.get(key) or "").strip()
            if not candidate:
                continue
            for form in (candidate, sanitize_work_title(candidate)):
                qid = known_work_qid_for_title(form)
                if qid:
                    return qid
    return ""


def _strip_ms_scope_suffix(title: str) -> str:
    return re.sub(
        r"\s*\(MS\s+[\d\"']+\)\s*$",
        "",
        str(title or "").strip(),
        flags=re.IGNORECASE,
    ).strip()


def _authority_work_match(
    title: str,
    context: CanonicalStudioContext,
) -> dict[str, Any] | None:
    from converter.rdf.rdf_helpers import sanitize_work_title  # noqa: PLC0415

    keys = {
        title.casefold(),
        sanitize_work_title(title).casefold().strip(" .,;:/-\"'"),
        _strip_ms_scope_suffix(title).casefold(),
    }
    work_roles = {
        "work", "contained_work", "contained work", "related_work",
        "related work", "subject",
    }
    for key in keys:
        if not key:
            continue
        match = context.matches_by_name.get(key)
        if not match:
            continue
        kind = str(match.get("entity_kind") or "").casefold().replace("-", "_")
        role = str(match.get("role") or "").casefold().replace("-", "_")
        if kind == "work" or role in work_roles or role.replace(" ", "_") in {
            "contained_work", "related_work",
        }:
            return match
    return None


def _contents_row_fields(
    content: object,
    parse_contents_entry,
) -> tuple[str, str, object, str, str]:
    if isinstance(content, dict):
        raw = str(
            content.get("raw")
            or content.get("source_text")
            or content.get("title")
            or content.get("text")
            or ""
        ).strip()
        parsed = parse_contents_entry(raw) if raw else {}
        content_title = str(
            content.get("title") or content.get("text") or parsed.get("title") or ""
        ).strip()
        if not content_title and parsed.get("title"):
            content_title = str(parsed["title"]).strip()
        folio = str(
            content.get("folio")
            or content.get("folio_range")
            or parsed.get("folio_range")
            or ""
        )
        sequence = content.get("sequence") or content.get("seq") or parsed.get("sequence")
        source_text = raw or content_title
        source_field = str(content.get("source_field") or "505").strip().upper() or "505"
        if source_field not in {"505", "500"}:
            source_field = "505"
        return content_title, folio, sequence, source_text, source_field

    raw = str(content).strip()
    parsed = parse_contents_entry(raw) if raw else {}
    content_title = str(parsed.get("title") or raw).strip()
    return (
        content_title,
        str(parsed.get("folio_range") or ""),
        parsed.get("sequence"),
        raw or content_title,
        "505",
    )


_TITLE_MATCH_STRICT = "strict"
_TITLE_MATCH_LOOSE = "loose"
# A loose match needs enough text to mean anything. "מחזור" matching
# "מחזור מנהג אשכנז המערבי (וורמיזא) לכל השנה" is how QDraft_Work_37 bound to the
# wrong record.
_LOOSE_MATCH_MIN_CHARS = 8
_LOOSE_MATCH_MIN_TOKENS = 2


def _normalise_work_title(text: str) -> str:
    from converter.rdf.rdf_helpers import sanitize_work_title  # noqa: PLC0415

    return _strip_ms_scope_suffix(
        sanitize_work_title(str(text or "")),
    ).casefold().strip(" .,;:/-\"'")


def _isbd_stem(text: str) -> str:
    """The part before an ISBD subtitle break, so `A : B` matches `A`."""
    for sep in (" : ", ". ", " — ", " - "):
        head, found, _tail = text.partition(sep)
        if found and head.strip():
            return head.strip(" .,;:/-\"'")
    return text


def _titles_match(left: str, right: str, *, mode: str = _TITLE_MATCH_STRICT) -> bool:
    """Do two titles name the same work?

    ``strict`` is the only mode allowed to establish identity or to ACCEPT
    evidence: equality after work-title sanitisation, optionally up to an ISBD
    subtitle break. ``loose`` keeps the old substring behaviour for discovery and
    ranking only, floored so a single short word cannot match a long title
    (Rule W-165).
    """
    a = _normalise_work_title(left)
    b = _normalise_work_title(right)
    if not a or not b:
        return False
    if a == b:
        return True
    if _isbd_stem(a) == _isbd_stem(b):
        return True
    if mode == _TITLE_MATCH_STRICT:
        return False
    shorter = a if len(a) <= len(b) else b
    if len(shorter) < _LOOSE_MATCH_MIN_CHARS:
        return False
    if len(shorter.split()) < _LOOSE_MATCH_MIN_TOKENS:
        return False
    return a in b or b in a


def _upload_descriptions(
    entity: CanonicalHmoEntity,
    entity_type: str,
    *,
    context: CanonicalStudioContext | None = None,
) -> dict[str, str]:
    # Manuscripts: the generated form wins. HMO manuscript descriptions are
    # `rdfs:comment` catalog notes ("According to Louis Levin…", "Related
    # material: AHW-14", RDA 33x carrier terms) — evidence, not descriptions.
    # Every manuscript carrying one was judged partial/fail; the generated
    # "<language> manuscript, <date>, <holder>" form passed (Rule W-137).
    if entity_type == "manuscript" and context is not None:
        enriched = _enriched_description(entity, entity_type, context)
        if enriched:
            return enriched

    cleaned: dict[str, str] = {}
    for lang, value in entity.descriptions.items():
        text = str(value or "").strip()
        if not text or _is_boilerplate_description(text):
            continue
        if entity_type == "manuscript" and _is_catalog_note_description(text):
            continue
        slot = _description_language_slot(str(lang), text)
        if not slot:
            continue
        cleaned.setdefault(slot, _truncate_description(text))
    if cleaned:
        return cleaned

    if context is not None:
        enriched = _enriched_description(entity, entity_type, context)
        if enriched:
            return enriched

    if entity_type == "manuscript":
        return {"en": _truncate_description("Hebrew manuscript, National Library of Israel")}
    if entity_type == "person":
        return {"en": _truncate_description("person associated with Hebrew manuscripts")}
    if entity_type == "work":
        return {"en": _truncate_description("Work preserved in a Hebrew manuscript")}
    return {}


_HEBREW_CHARS = re.compile(r"[֐-׿]")
_LATIN_CHARS = re.compile(r"[A-Za-z]")

# Israeli holders whose Hebrew name is the institution's *own* name, not a
# translation. Foreign institutions intentionally keep their Latin name here
# rather than have us invent a Hebrew form — extend this map only with a
# name the institution itself uses (Rule W-140).
def _description_language_slot(lang: str, text: str) -> str:
    """Route a description to the language it is actually written in.

    45 work items shipped Hebrew text in the ``en`` slot (Rule W-69 covered the
    generated form only, not stored HMO comments). A mixed-script description
    cannot be trusted as either language, so it is dropped and the generated
    form takes over (Rule W-137).
    """
    hebrew = bool(_HEBREW_CHARS.search(text))
    latin = bool(_LATIN_CHARS.search(text))
    if hebrew and latin:
        return "" if lang == "en" else lang
    if hebrew:
        return "he"
    if latin:
        return "en"
    return lang


def _hebrew_manuscript_label(record: dict[str, Any] | None, suffix: str) -> str:
    """`he` designation label — delegates to the one builder in the mirror."""
    from converter.wikidata.item_builder import (  # noqa: PLC0415
        manuscript_he_designation,
    )

    return manuscript_he_designation(record, suffix)


def _hebrew_manuscript_description(record: dict[str, Any]) -> str:
    """Hebrew counterpart of the generated manuscript description.

    Keeps the ``he`` slot from falling back to a MARC note (Rule W-137). Only
    evidenced fragments are used — no invented date or holder.
    """
    from converter.wikidata.item_builder import (  # noqa: PLC0415
        HEBREW_INSTITUTION_NAMES,
        _LANG_CODE_TO_HEBREW,
        _holding_institution_name,
    )

    # The language word follows the record, exactly as the `en` description
    # does — a hardcoded "עברי" made 10 Arabic/Italian manuscripts contradict
    # their own English description (Rule W-140).
    from converter.wikidata.item_builder import (  # noqa: PLC0415
        _is_printed_facsimile_record,
    )

    languages = record.get("languages") or []
    primary = str(languages[0]) if languages else "heb"
    language_word = _LANG_CODE_TO_HEBREW.get(primary, "עברי")
    # A photostatic reprint is not a manuscript; the `en` description already
    # said so while `he` still called it כתב יד (Rule W-142 / W-77).
    parts = [
        f"מהדורת פקסימיליה מודפסת {language_word}"
        if _is_printed_facsimile_record(record)
        else f"כתב יד {language_word}"
    ]
    dates = record.get("dates")
    if isinstance(dates, dict):
        # Same precision gate as the English description (Rule W-164 / W-170):
        # never put a century midpoint (1001/1501) into the Hebrew description.
        from converter.wikidata.item_builder import _description_date_fragment  # noqa: PLC0415

        fragment = _description_date_fragment(dates)
        if fragment and re.match(r"\d{3,4}$", fragment):
            parts.append(fragment)
        elif fragment and "century" in fragment:
            # Keep HE free of English century prose; omit rather than invent מאה.
            pass
        # else: Hebrew-only century in original_string already handled by fragment
        # returning an English century string — omit from he description.
    holder = _holding_institution_name(record)
    if holder:
        parts.append(HEBREW_INSTITUTION_NAMES.get(holder, holder))
    shelfmark = str(record.get("shelfmark") or "").strip().strip('"')
    if shelfmark:
        parts.append(shelfmark)
    if len(parts) == 1:
        return ""
    return ", ".join(parts)


def _enriched_description(
    entity: CanonicalHmoEntity,
    entity_type: str,
    context: CanonicalStudioContext,
) -> dict[str, str]:
    from converter.wikidata.item_builder import (  # noqa: PLC0415
        _build_manuscript_description,
        _build_person_description,
        _build_work_description,
    )

    if entity_type == "manuscript":
        # Its own record only — a linked manuscript's title/date/holder must
        # never describe this one (Rule W-137).
        cn = identity_control_number(entity)
        record = context.marc_by_cn.get(cn) if cn else None
        if record:
            marc = dict(record)
            marc["_control_number"] = cn
            out = {"en": _truncate_description(_build_manuscript_description(marc))}
            hebrew = _hebrew_manuscript_description(marc)
            if hebrew:
                out["he"] = _truncate_description(hebrew)
            return out

    if entity_type == "person":
        match = _authority_match_for_entity(entity, context)
        if match:
            role = str(match.get("role") or "").strip()
            # Dates only from an identifier-backed match. A name-keyed match is
            # a string coincidence: it described a scribe of a 1642 manuscript
            # as "(1786-1874)" — a homonym's lifespan (Rule W-138 / W-67).
            dated_match = _authority_match_for_entity(
                entity, context, identifier_only=True,
            )
            dates = _authority_date_range(dated_match) if dated_match else ""
            return {
                "en": _truncate_description(
                    _build_person_description(
                        role=role,
                        dates_str=dates,
                        is_org=str(match.get("entity_kind") or "").lower() == "organization",
                    ),
                ),
            }

    if entity_type == "work":
        title = entity.labels.get("he") or entity.labels.get("en") or ""
        # The attestation clause must cite the record this work is anchored to, not
        # the first control number that happens to index. It also used to look the
        # CN up as a raw string rather than canonicalising it (Rule W-66), and the
        # `title` computed here was never consulted at all (Rule W-165).
        anchor_cn, anchor_record = _work_identity_record(entity, context)
        if anchor_record is not None:
            marc = dict(anchor_record)
            marc["_control_number"] = anchor_cn
            return {
                "en": _truncate_description(
                    _work_description_with_attestation(entity, marc, context),
                ),
            }
        if title:
            # No anchor: describe the work, but claim no attestation.
            return {
                "en": _truncate_description(
                    _build_work_description(author_name=None, century=None),
                ),
            }
    return {}


def _work_description_with_attestation(
    entity: CanonicalHmoEntity,
    marc: dict[str, Any],
    context: CanonicalStudioContext,
) -> str:
    """Disambiguate a work by century, author and the manuscript attesting it.

    82 works shipped the bare fallback `"Work preserved in a Hebrew
    manuscript"`, which disambiguates nothing — Wikidata descriptions exist to
    tell same-label items apart. Every fragment here is evidence-derived
    (Rule W-138).
    """
    from converter.wikidata.item_builder import (  # noqa: PLC0415
        _build_work_description,
        _has_hebrew_script,
    )

    author_name = ""
    match = _authority_match_for_entity(entity, context, identifier_only=True)
    if match:
        payload = match.get("payload") or {}
        if isinstance(payload, Mapping):
            candidate = str(payload.get("preferred_name_lat") or "").strip()
            if candidate and not _has_hebrew_script(candidate):
                author_name = candidate

    description = _build_work_description(
        author_name=author_name or None,
        century=_century_hint_from_record(marc),
    )

    # Cite the control number, not the shelfmark: it is in the item's own
    # record_ids / P3959 and therefore verifiable from the evidence pack
    # (Rule W-138 follow-up — a cited shelfmark read as unsupported on all 105
    # works because a work's evidence does not carry one).
    control_number = canonical_control_number(marc.get("_control_number"))
    if control_number:
        description = f"{description}, attested in NLI record {control_number}"
    return description


def _authority_date_range(match: Mapping[str, Any]) -> str:
    """`birth-death` from an authority payload, or empty when unknown."""
    payload = match.get("payload") or {}
    if not isinstance(payload, Mapping):
        return ""
    birth = payload.get("birth_year")
    death = payload.get("death_year")
    if not birth and not death:
        return ""
    return f"{birth or '?'}-{death or '?'}"


def _authority_match_for_entity(
    entity: CanonicalHmoEntity,
    context: CanonicalStudioContext,
    *,
    identifier_only: bool = False,
) -> dict[str, Any] | None:
    """Resolve the authority match behind an entity.

    ``identifier_only`` refuses the label fallback. A name-keyed match is a
    string coincidence, not an identity: it gave a scribe in a 1642 manuscript
    the dates ``(1786-1874)`` from a homonym. Anything that asserts biographical
    fact (P569/P570 or a dated description) MUST pass ``identifier_only=True``
    (Rule W-138, and Rule W-67's exact-ID requirement).
    """
    for evidence in entity.authority_evidence:
        if evidence.get("accepted") is not True:
            continue
        kind = str(evidence.get("kind") or "").lower()
        identifier = _evidence_identifier(evidence)
        if kind == "wikidata":
            qid = normalize_wikidata_qid(identifier)
            if qid and qid in context.matches_by_qid:
                return context.matches_by_qid[qid]
        if kind == "viaf":
            viaf = normalize_viaf_id(identifier)
            if viaf and viaf in context.matches_by_viaf:
                return context.matches_by_viaf[viaf]
        if kind == "mazal" and identifier in context.matches_by_mazal:
            return context.matches_by_mazal[identifier]
    for claim in entity.claims:
        property_name = _property_local_name(str(claim.get("property_uri") or ""))
        value = _claim_scalar_value(claim)
        if property_name == "viaf_id":
            viaf = normalize_viaf_id(value)
            if viaf and viaf in context.matches_by_viaf:
                return context.matches_by_viaf[viaf]
        if property_name in {"external_identifier_nli", "external_uri_nli", "authority_id", "mazal_id"}:
            mazal = normalize_authority_id(value)
            if mazal and mazal in context.matches_by_mazal:
                return context.matches_by_mazal[mazal]
    if identifier_only:
        return None
    for label in entity.labels.values():
        key = str(label or "").strip().casefold()
        if key and key in context.matches_by_name:
            return context.matches_by_name[key]
    return None


def _century_hint_from_record(record: Mapping[str, Any]) -> str | None:
    dates = record.get("dates") or {}
    if not isinstance(dates, Mapping):
        return None
    original = str(dates.get("original_string") or "").strip()
    match = re.search(r"\d{1,2}(?:th|st|nd|rd)\s*century", original, re.IGNORECASE)
    return match.group(0).lower() if match else None


def _append_manuscript_bridge_statements(
    entity: CanonicalHmoEntity,
    entity_type: str,
    statements: list[WikidataStatement],
) -> None:
    if entity_type != "manuscript" or not entity.control_numbers:
        return
    control_number = identity_control_number(entity)
    if not control_number:
        return
    # Drop ontology IRIs / dead MS_ slugs that may have been rolled up from
    # live HMO claims before attaching the browseable Item:Q bridge.
    kept: list[WikidataStatement] = []
    for stmt in statements:
        if stmt.property_id in (P_EXACT_MATCH, P_DESCRIBED_AT_URL):
            if not is_browseable_hmo_wikibase_url(str(stmt.value or "")):
                continue
        kept.append(stmt)
    statements.clear()
    statements.extend(kept)

    wikibase_url = resolve_hmo_bridge_url(
        control_number,
        {control_number: entity.wikibase_id},
        wikibase_qid=entity.wikibase_id,
    )
    if not wikibase_url:
        return
    existing = {(stmt.property_id, str(stmt.value or "")) for stmt in statements}
    if (P_EXACT_MATCH, wikibase_url) not in existing:
        statements.append(WikidataStatement(property_id=P_EXACT_MATCH, value=wikibase_url, value_type="url"))
    if (P_DESCRIBED_AT_URL, wikibase_url) not in existing:
        statements.append(WikidataStatement(property_id=P_DESCRIBED_AT_URL, value=wikibase_url, value_type="url"))
