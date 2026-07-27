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
from app.pipeline.marc_verify_context import canonical_control_number
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
    hmo_wikibase_entity_url,
    hmo_wikibase_page_url,
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
    marc_by_cn: dict[str, dict[str, Any]] = {}
    for raw in marc_records or []:
        record = dict(raw)
        cn = canonical_control_number(
            str(record.get("_control_number") or record.get("control_number") or ""),
        )
        if cn:
            marc_by_cn[cn] = record

    matches_by_viaf: dict[str, dict[str, Any]] = {}
    matches_by_mazal: dict[str, dict[str, Any]] = {}
    matches_by_qid: dict[str, dict[str, Any]] = {}
    matches_by_name: dict[str, dict[str, Any]] = {}
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
            name = str(match.get(key) or "").strip().casefold()
            if name:
                matches_by_name[name] = match
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


def canonical_wikidata_fingerprint(entities: Iterable[CanonicalHmoEntity]) -> str:
    candidates = wikidata_candidates_from_hmo(entities)
    payload = json.dumps(candidates, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(("hmo-wikidata-v5:" + payload).encode()).hexdigest()


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

    for cn in entity.control_numbers:
        cn = str(cn).strip()
        if cn and wd_type == "manuscript":
            key = (P_NLI_CATALOG_ID, cn)
            if key not in seen:
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
        rollup_sources = _rollup_sources_for(entity, wd_type, all_entities, entities_by_cn)
        statements = [
            WikidataStatement(
                property_id=claim["property"],
                value=claim["value"],
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
            records=list(entity.control_numbers),
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
) -> dict[str, Any]:
    """Build serialised Wikidata Studio items from durable HMO snapshots."""
    from app.pipeline import wikidata_studio  # noqa: PLC0415
    from app.pipeline.wikidata_upload import _reconcile_sync  # noqa: PLC0415
    from converter.wikidata.item_validator import validate_item  # noqa: PLC0415
    from converter.wikidata.quickstatements import QuickStatementsExporter  # noqa: PLC0415

    materialized = list(entities)
    uploadable = uploadable_entities_from_hmo(materialized)
    rollup_stats = _rollup_summary_stats(materialized, uploadable)
    native_items = native_items_from_hmo(materialized, context=context)
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
            "source_uri": entity.source_uri if entity else None,
            "hmo_wikibase_id": entity.wikibase_id if entity else None,
            "projection_source": "hmo_wikibase",
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
        },
    }


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
        return {"en": f"NLI manuscript {entity.control_numbers[0]}"}, aliases
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

    if title_clean and not placeholder:
        if title_has_hebrew:
            labels["he"] = title_clean
        else:
            labels["en"] = title_clean
            aliases.setdefault("en", []).append(title_clean)
    elif title_clean:
        aliases.setdefault("he", []).append(title_clean)

    shelfmark = ""
    if record:
        shelfmark = normalise(str(record.get("shelfmark") or ""))
    if not shelfmark:
        shelfmark = _shelfmark_from_claims(entity)
    if shelfmark:
        holding = holding_name(record) if record else ""
        labels["en"] = f"{holding or 'Jerusalem, NLI'}, {shelfmark}"
        if title_clean and not placeholder and title_has_hebrew:
            aliases.setdefault("he", []).append(title_clean)
        if "he" not in labels and (placeholder or not title_has_hebrew):
            labels["he"] = f"כתב יד עברי, ספרייה לאומית, {shelfmark}"

    if "en" not in labels and "he" not in labels:
        cn = ""
        if record:
            cn = str(record.get("_control_number") or record.get("control_number") or "").strip()
        if not cn and entity.control_numbers:
            cn = str(entity.control_numbers[0]).strip()
        if cn:
            labels["en"] = f"Jerusalem, NLI, {cn}"
            labels["he"] = f"כתב יד עברי, ספרייה לאומית, {cn}"
        else:
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
        cleaned = normalise(strip_qualifiers(name))
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
        _place(primary)

    for lang, value in raw_labels.items():
        text = str(value).strip()
        if text and text != primary:
            _place(text, as_alias=lang in labels)

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
    if context is None:
        return None
    for raw_cn in entity.control_numbers:
        cn = canonical_control_number(str(raw_cn))
        record = context.marc_by_cn.get(cn) if cn else None
        if record:
            return dict(record)
    return None


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
    from converter.rdf.rdf_helpers import sanitize_work_title  # noqa: PLC0415
    from converter.wikidata.work_candidates import assess_work_candidate  # noqa: PLC0415

    title = sanitize_work_title(
        str(
            (entity.labels or {}).get("he")
            or (entity.labels or {}).get("en")
            or ""
        ).strip()
    )
    if not title:
        return []

    known_qid = _accepted_wikidata_qid(entity)

    if context is not None:
        match = context.matches_by_name.get(title.casefold())
        if match and str(match.get("entity_kind") or match.get("role") or "").lower() in {
            "work", "contained_work", "subject",
        }:
            # matches_by_name only holds approved matches from the build path
            decision = assess_work_candidate(
                title,
                source_field="AUTHORITY",
                approved=True,
                known_qid=normalize_wikidata_qid(match.get("wikidata_qid")) or known_qid,
                source_text=str(match.get("entity_text") or match.get("matched_name") or title),
            )
            if decision.accepted:
                return [decision.evidence()]

        for raw_cn in entity.control_numbers:
            record = context.marc_by_cn.get(canonical_control_number(str(raw_cn)) or "")
            if not record:
                continue
            for content in record.get("contents") or []:
                if isinstance(content, dict):
                    content_title = str(
                        content.get("title") or content.get("text") or ""
                    ).strip()
                    folio = str(content.get("folio") or content.get("folio_range") or "")
                    sequence = content.get("sequence") or content.get("seq")
                    source_text = str(content.get("raw") or content.get("source_text") or content_title)
                else:
                    content_title = str(content).strip()
                    folio = ""
                    sequence = None
                    source_text = content_title
                if not _titles_match(title, content_title):
                    continue
                decision = assess_work_candidate(
                    content_title or title,
                    source_field="505",
                    known_qid=known_qid,
                    folio_range=folio,
                    sequence=sequence,
                    source_text=source_text,
                )
                if decision.accepted:
                    return [decision.evidence()]

            for mention in record.get("work_mentions") or []:
                if isinstance(mention, dict):
                    mention_title = str(
                        mention.get("title") or mention.get("text") or ""
                    ).strip()
                    kind = str(mention.get("kind") or mention.get("candidate_kind") or "named_work")
                    source_text = str(mention.get("source_text") or mention_title)
                else:
                    mention_title = str(mention).strip()
                    kind = "named_work"
                    source_text = mention_title
                if not _titles_match(title, mention_title):
                    continue
                decision = assess_work_candidate(
                    mention_title or title,
                    source_field="500",
                    known_qid=known_qid,
                    candidate_kind=kind,
                    source_text=source_text,
                )
                if decision.accepted:
                    return [decision.evidence()]

            for related in record.get("related_works") or []:
                if isinstance(related, dict):
                    related_title = str(
                        related.get("title") or related.get("text") or ""
                    ).strip()
                    related_qid = normalize_wikidata_qid(
                        related.get("wikidata_qid") or related.get("qid")
                    )
                    source_text = str(related.get("source_text") or related_title)
                else:
                    related_title = str(related).strip()
                    related_qid = None
                    source_text = related_title
                if not _titles_match(title, related_title):
                    continue
                decision = assess_work_candidate(
                    related_title or title,
                    source_field="RELATED",
                    known_qid=related_qid or known_qid,
                    approved=bool(related_qid),
                    source_text=source_text,
                )
                if decision.accepted:
                    return [decision.evidence()]

    if known_qid:
        decision = assess_work_candidate(
            title,
            source_field="HMO",
            known_qid=known_qid,
            source_text=title,
        )
        if decision.accepted:
            return [decision.evidence()]

    return []


def _titles_match(left: str, right: str) -> bool:
    from converter.rdf.rdf_helpers import sanitize_work_title  # noqa: PLC0415

    a = sanitize_work_title(str(left or "")).casefold().strip(" .,;:/-\"'")
    b = sanitize_work_title(str(right or "")).casefold().strip(" .,;:/-\"'")
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _upload_descriptions(
    entity: CanonicalHmoEntity,
    entity_type: str,
    *,
    context: CanonicalStudioContext | None = None,
) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for lang, value in entity.descriptions.items():
        text = str(value or "").strip()
        if text and not _is_boilerplate_description(text):
            cleaned[str(lang)] = _truncate_description(text)
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
        for cn in entity.control_numbers:
            record = context.marc_by_cn.get(str(cn).strip())
            if not record:
                continue
            marc = dict(record)
            marc["_control_number"] = str(cn).strip()
            return {"en": _truncate_description(_build_manuscript_description(marc))}

    if entity_type == "person":
        match = _authority_match_for_entity(entity, context)
        if match:
            payload = match.get("payload") or {}
            role = str(match.get("role") or "").strip()
            birth = payload.get("birth_year")
            death = payload.get("death_year")
            dates = ""
            if birth or death:
                dates = f"{birth or '?'}-{death or '?'}"
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
        for cn in entity.control_numbers:
            record = context.marc_by_cn.get(str(cn).strip())
            if not record:
                continue
            marc = dict(record)
            marc["_control_number"] = str(cn).strip()
            century = _century_hint_from_record(marc)
            return {
                "en": _truncate_description(
                    _build_work_description(author_name=None, century=century),
                ),
            }
        if title:
            return {
                "en": _truncate_description(
                    _build_work_description(author_name=None, century=None),
                ),
            }
    return {}


def _authority_match_for_entity(
    entity: CanonicalHmoEntity,
    context: CanonicalStudioContext,
) -> dict[str, Any] | None:
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
    control_number = str(entity.control_numbers[0]).strip()
    if not control_number:
        return
    wikibase_url = hmo_wikibase_entity_url(control_number, {control_number: entity.wikibase_id}) or hmo_wikibase_page_url(control_number)
    if not wikibase_url:
        return
    existing = {(stmt.property_id, str(stmt.value or "")) for stmt in statements}
    if (P_EXACT_MATCH, wikibase_url) not in existing:
        statements.append(WikidataStatement(property_id=P_EXACT_MATCH, value=wikibase_url, value_type="url"))
    if (P_DESCRIBED_AT_URL, wikibase_url) not in existing:
        statements.append(WikidataStatement(property_id=P_DESCRIBED_AT_URL, value=wikibase_url, value_type="url"))
