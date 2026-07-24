"""Safe preparation of Wikidata candidates from canonical HMO entities."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
import hashlib
import json
import logging
import re

from app.pipeline.hmo_canonical import CanonicalHmoEntity, assert_canonical_entities
from converter.authority.evidence import normalize_authority_id, normalize_viaf_id, normalize_wikidata_qid
from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.projection_coverage import STRATEGY_BY_LOCAL_NAME
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

_PROPERTY_ID = re.compile(r"^P[1-9][0-9]*$")
_QID = re.compile(r"^Q[1-9][0-9]*$")
_MAX_DESCRIPTION_LENGTH = 250

_BOILERPLATE_DESCRIPTION = (
    re.compile(r"^Offline HMO Wikibase draft for ", re.I),
    re.compile(r"^Linked to \d+ manuscripts", re.I),
    re.compile(r" linked to manuscript \d+\.?$", re.I),
    re.compile(r" in the Hebrew manuscripts corpus\.?$", re.I),
    re.compile(r"^(F\d+_|E\d+_)[A-Za-z_ ]+\.?"),
)

_HMO_PROPERTY_TO_WIKIDATA_PID: dict[str, str] = {
    "viaf_id": "P214",
    "external_identifier_nli": "P8189",
    "external_uri_nli": "P8189",
    "authority_id": "P8189",
    "mazal_id": "P8189",
    "shelfmark": "P217",
    "inventory_number": "P217",
    "has_language": "P407",
    "has_script_type": "P9302",
    "has_material": "P186",
    "has_number_of_folios": "P1104",
    "has_title": "P1476",
    "has_genre": "P136",
    "has_main_subject": "P921",
    "has_iiif_manifest_url": "P6108",
    "has_described_at_url": "P973",
    "has_full_work_url": "P953",
    "has_copyright_status": "P6216",
    "has_condition": "P5816",
    "has_date_of_creation": "P571",
    "has_location_of_creation": "P1071",
    "has_occupation": "P106",
    "has_date_of_birth": "P569",
    "has_date_of_death": "P570",
    "has_native_name": "P1559",
}

_INSTANCE_QIDS: dict[str, str] = {
    "F4_Manifestation_Singleton": Q_MANUSCRIPT,
    "Manuscript": Q_MANUSCRIPT,
    "F1_Work": Q_WRITTEN_WORK,
    "Work": Q_WRITTEN_WORK,
    "E21_Person": Q_HUMAN,
    "Person": Q_HUMAN,
}

_PERSON_IDENTIFIER_PIDS = frozenset({"P214", "P8189", "P244", "P227", "P213", "P268"})


def wikidata_candidates_from_hmo(
    entities: Iterable[CanonicalHmoEntity],
) -> list[dict[str, Any]]:
    """Return projection records grounded exclusively in HMO state."""
    materialized = uploadable_entities_from_hmo(entities)
    assert_canonical_entities(materialized)
    return [
        {
            "local_id": entity.local_id,
            "source_uri": entity.source_uri,
            "hmo_wikibase_id": entity.wikibase_id,
            "labels": dict(entity.labels),
            "descriptions": dict(entity.descriptions),
            "aliases": dict(entity.aliases),
            "claims": list(entity.claims),
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
    return hashlib.sha256(("hmo-wikidata-v2:" + payload).encode()).hexdigest()


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


def native_wikidata_claims(entity: CanonicalHmoEntity) -> list[dict[str, str]]:
    native: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    wd_type = _wikidata_entity_type(entity) or ""

    for claim in entity.claims:
        property_id = str(claim.get("wikidata_property") or "").strip()
        if not _PROPERTY_ID.fullmatch(property_id):
            property_name = _property_local_name(str(claim.get("property_uri") or ""))
            property_id = _HMO_PROPERTY_TO_WIKIDATA_PID.get(property_name, "")
        if not _PROPERTY_ID.fullmatch(property_id):
            continue

        raw = _claim_scalar_value(claim)
        if claim.get("value_type") == "wikibase-item" or claim.get("target_uri"):
            raw = str(claim.get("target_qid") or claim.get("wikidata_value") or raw).strip()
            if not _QID.fullmatch(raw):
                target_name = _property_local_name(str(claim.get("target_uri") or ""))
                raw = _INSTANCE_QIDS.get(target_name, "")
        if not raw:
            continue
        if property_id == "P214" and wd_type != "person":
            continue
        if property_id == "P8189" and wd_type != "person":
            continue
        if property_id == P_NLI_CATALOG_ID and wd_type != "manuscript":
            continue
        if property_id == "P8189" and not str(raw).startswith("9870"):
            if wd_type == "manuscript" and str(raw).startswith("990"):
                property_id = P_NLI_CATALOG_ID
            else:
                continue
        key = (property_id, str(raw))
        if key in seen:
            continue
        seen.add(key)
        native.append({"property": property_id, "value": str(raw)})

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


def native_items_from_hmo(entities: Iterable[CanonicalHmoEntity]) -> list[WikidataItem]:
    """Adapt live HMO snapshots to the guarded Wikidata upload model."""
    materialized = uploadable_entities_from_hmo(entities)
    assert_canonical_entities(materialized)
    items: list[WikidataItem] = []
    for entity in materialized:
        wd_type = _wikidata_entity_type(entity) or ""
        existing_qid = _accepted_wikidata_qid(entity)
        statements = [
            WikidataStatement(
                property_id=claim["property"],
                value=claim["value"],
                value_type="wikibase-item" if _QID.fullmatch(claim["value"]) else "string",
            )
            for claim in native_wikidata_claims(entity)
        ]
        _append_manuscript_bridge_statements(entity, wd_type, statements)
        items.append(WikidataItem(
            labels=_upload_labels(entity),
            descriptions=_upload_descriptions(entity, wd_type),
            aliases=dict(entity.aliases),
            statements=statements,
            existing_qid=existing_qid,
            entity_type=wd_type,
            local_id=entity.local_id,
            records=list(entity.control_numbers),
            authority_evidence=[
                evidence for evidence in entity.authority_evidence
                if evidence.get("accepted") is True
            ],
        ))
    return items


def build_canonical_studio_result(
    entities: Iterable[CanonicalHmoEntity],
    *,
    overrides: dict[str, dict[str, Any]] | None = None,
    return_native: bool = False,
    reconcile: bool = True,
) -> dict[str, Any]:
    """Build serialised Wikidata Studio items from durable HMO snapshots."""
    from app.pipeline import wikidata_studio  # noqa: PLC0415
    from app.pipeline.wikidata_upload import _reconcile_sync  # noqa: PLC0415
    from converter.wikidata.item_validator import validate_item  # noqa: PLC0415
    from converter.wikidata.quickstatements import QuickStatementsExporter  # noqa: PLC0415

    materialized = list(entities)
    uploadable = uploadable_entities_from_hmo(materialized)
    native_items = native_items_from_hmo(uploadable)

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
    for item_dict, entity, issues in zip(serialised, uploadable, per_item_issues, strict=True):
        item_dict.update({
            "source_uri": entity.source_uri,
            "hmo_wikibase_id": entity.wikibase_id,
            "projection_source": "hmo_wikibase",
            "source_fingerprint": entity.source_fingerprint,
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
            "skipped_entities": max(0, len(materialized) - len(uploadable)),
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


def _default_instance_qid(entity_type: str) -> str:
    if entity_type == "manuscript":
        return Q_MANUSCRIPT
    if entity_type == "work":
        return Q_WRITTEN_WORK
    if entity_type == "person":
        return Q_HUMAN
    return ""


def _property_local_name(property_uri: str) -> str:
    text = str(property_uri or "").strip()
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rstrip("/").rsplit("/", 1)[-1]


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
    labels = {lang: str(value).strip() for lang, value in entity.labels.items() if str(value).strip()}
    if labels:
        return labels
    if entity.control_numbers:
        return {"en": f"NLI manuscript {entity.control_numbers[0]}"}
    return {"en": entity.local_id.replace("QDraft_", "").replace("_", " ")}


def _upload_descriptions(entity: CanonicalHmoEntity, entity_type: str) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for lang, value in entity.descriptions.items():
        text = str(value or "").strip()
        if text and not _is_boilerplate_description(text):
            cleaned[str(lang)] = _truncate_description(text)
    if cleaned:
        return cleaned

    if entity_type == "manuscript":
        return {"en": _truncate_description("Hebrew manuscript, National Library of Israel")}
    if entity_type == "person":
        return {"en": _truncate_description("person associated with Hebrew manuscripts")}
    if entity_type == "work":
        return {"en": _truncate_description("Work preserved in a Hebrew manuscript")}
    return {}


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
