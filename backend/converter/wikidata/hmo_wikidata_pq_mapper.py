"""Project HMO Wikibase → public Wikidata P/Q mapper.

Project Wikibase IDs on ``mhm-hmo.wikibase.cloud`` (local ``P7``, ``Q1252``, …)
are **not** Wikidata IDs. This module is the only allowlisted bridge:

1. Ontology local-name / URI → public Wikidata PID / class QID
2. Project Wikibase PID → public Wikidata PID **only** via the schema ledger
   ontology URI (never by numeric identity)
3. Item values → public Wikidata QID only from an explicit Wikidata URI/QID
   or a mapped ontology **class** URI — never from a bare project QID

Contract: ``docs/wikidata-manuscripts-data-model.md`` + Rule W-83 / W-100.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from converter.authority.evidence import normalize_wikidata_qid
from converter.wikidata.property_mapping import (
    P_ANNOTATOR,
    P_AUTHOR,
    P_COLLECTION,
    P_COMMISSIONED_BY,
    P_CONDITION,
    P_COPYRIGHT_STATUS,
    P_DATE_OF_BIRTH,
    P_DATE_OF_DEATH,
    P_DESCRIBED_AT_URL,
    P_EXACT_MATCH,
    P_EXEMPLAR_OF,
    P_FULL_WORK_URL,
    P_GENRE,
    P_GEONAMES_ID,
    P_HEIGHT,
    P_IIIF_MANIFEST,
    P_INCEPTION,
    P_INSCRIPTION,
    P_INSTANCE_OF,
    P_INVENTORY_NUMBER,
    P_LANGUAGE,
    P_LOCATION_OF_CREATION,
    P_MAIN_SUBJECT,
    P_MATERIAL,
    P_NLI_CATALOG_ID,
    P_NLI_J9U_ID,
    P_NUMBER_OF_PAGES,
    P_OCCUPATION,
    P_OWNED_BY,
    P_SCRIPT_STYLE,
    P_TITLE,
    P_TRANSCRIBED_BY,
    P_VIAF_ID,
    P_WIDTH,
    P_WRITING_SYSTEM,
    Q_HUMAN,
    Q_MANUSCRIPT,
    Q_ORGANIZATION,
    Q_WRITTEN_WORK,
)

_PROPERTY_ID = re.compile(r"^P[1-9][0-9]*$")
_QID = re.compile(r"^Q[1-9][0-9]*$")
_WIKIDATA_ENTITY_RE = re.compile(
    r"(?:wikidata\.org/(?:entity|wiki)/|www\.wikidata\.org/(?:entity|wiki)/)"
    r"(Q[1-9][0-9]*)",
    re.I,
)

# Ontology local-name → public Wikidata PID (WPM / DS aligned).
# Deliberately omits HMO-only philology predicates (tradition, stemma, …).
HMO_LOCAL_NAME_TO_WIKIDATA_PID: dict[str, str] = {
    # Identifiers
    "viaf_id": P_VIAF_ID,
    "external_identifier_nli": P_NLI_J9U_ID,
    "external_uri_nli": P_NLI_J9U_ID,
    "authority_id": P_NLI_J9U_ID,
    "mazal_id": P_NLI_J9U_ID,
    "nli_catalog_id": P_NLI_CATALOG_ID,
    "control_number": P_NLI_CATALOG_ID,
    "shelfmark": P_INVENTORY_NUMBER,
    "inventory_number": P_INVENTORY_NUMBER,
    "geonames_id": P_GEONAMES_ID,
    "external_wikidata_uri": P_EXACT_MATCH,
    "hmo_source_uri": P_EXACT_MATCH,
    # Bibliographic / content
    "has_title": P_TITLE,
    "has_language": P_LANGUAGE,
    "has_genre": P_GENRE,
    "has_main_subject": P_MAIN_SUBJECT,
    "has_writing_system": P_WRITING_SYSTEM,
    "exemplar_of": P_EXEMPLAR_OF,
    "has_work": P_EXEMPLAR_OF,
    # Production / physical
    "has_date_of_creation": P_INCEPTION,
    "has_location_of_creation": P_LOCATION_OF_CREATION,
    "has_material": P_MATERIAL,
    "has_script_type": P_SCRIPT_STYLE,  # P9302 — not P6573 (TTL drift)
    "has_script_mode": P_SCRIPT_STYLE,
    "has_number_of_folios": P_NUMBER_OF_PAGES,  # P1104 + leaf unit at emit time
    "has_height_mm": P_HEIGHT,
    "has_width_mm": P_WIDTH,
    "has_condition": P_CONDITION,
    "has_copyright_status": P_COPYRIGHT_STATUS,
    # Digital access
    "has_iiif_manifest_url": P_IIIF_MANIFEST,
    "has_described_at_url": P_DESCRIBED_AT_URL,
    "has_full_work_url": P_FULL_WORK_URL,
    "has_digital_representation_url": P_FULL_WORK_URL,
    # Agents / roles (entity-type gates apply in the caller)
    "has_author": P_AUTHOR,
    "author": P_AUTHOR,
    "mentions_scribe": P_TRANSCRIBED_BY,
    "has_scribe": P_TRANSCRIBED_BY,
    "scribe": P_TRANSCRIBED_BY,
    "has_annotator": P_ANNOTATOR,
    "annotator": P_ANNOTATOR,
    "commissioned_by": P_COMMISSIONED_BY,
    "has_commissioner": P_COMMISSIONED_BY,
    "former_owner": P_OWNED_BY,
    "has_owner": P_OWNED_BY,
    "owned_by": P_OWNED_BY,
    "holding_institution": P_COLLECTION,
    "has_holding_institution": P_COLLECTION,
    "colophon_text": P_INSCRIPTION,
    # Person biography
    "has_occupation": P_OCCUPATION,
    "has_date_of_birth": P_DATE_OF_BIRTH,
    "has_date_of_death": P_DATE_OF_DEATH,
    "has_native_name": "P1559",
    "instance_of": P_INSTANCE_OF,
}

# Ontology class local-name → public Wikidata class QID (P31 / typed values).
HMO_CLASS_TO_WIKIDATA_QID: dict[str, str] = {
    "F4_Manifestation_Singleton": Q_MANUSCRIPT,
    "Manuscript": Q_MANUSCRIPT,
    "F1_Work": Q_WRITTEN_WORK,
    "Work": Q_WRITTEN_WORK,
    "E21_Person": Q_HUMAN,
    "Person": Q_HUMAN,
    "E74_Group": Q_ORGANIZATION,
    "Organization": Q_ORGANIZATION,
    "Group": Q_ORGANIZATION,
}

# PIDs that must never land on a manuscript item (WPM / Rule W-98).
MANUSCRIPT_FORBIDDEN_WIKIDATA_PIDS = frozenset({
    P_AUTHOR,  # P50 — use P1574 → work → P50
    "P17",     # country — not invented from holder
    "P131",    # admin location
})

# PIDs allowed only on persons.
PERSON_ONLY_WIKIDATA_PIDS = frozenset({
    P_VIAF_ID,
    P_NLI_J9U_ID,
    P_OCCUPATION,
    P_DATE_OF_BIRTH,
    P_DATE_OF_DEATH,
    "P1559",
})

# PIDs allowed only on manuscripts.
MANUSCRIPT_ONLY_WIKIDATA_PIDS = frozenset({
    P_NLI_CATALOG_ID,
    P_TRANSCRIBED_BY,
    P_ANNOTATOR,
    P_COMMISSIONED_BY,
    P_SCRIPT_STYLE,
    P_MATERIAL,
    P_NUMBER_OF_PAGES,
    P_HEIGHT,
    P_WIDTH,
    P_CONDITION,
    P_IIIF_MANIFEST,
    P_COLLECTION,
})


@dataclass(frozen=True)
class MappedWikidataClaim:
    property_id: str
    value: str
    value_type: str  # "wikibase-item" | "string" | "url" | "quantity" | …


def ontology_local_name(uri_or_name: str) -> str:
    text = str(uri_or_name or "").strip()
    if not text:
        return ""
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def is_wikidata_pid(value: str) -> bool:
    return bool(_PROPERTY_ID.fullmatch(str(value or "").strip()))


def is_wikidata_qid(value: str) -> bool:
    return bool(_QID.fullmatch(str(value or "").strip()))


def extract_wikidata_qid(value: Any) -> str | None:
    """Return a public Wikidata QID from a URI/Q-string, else None."""
    text = str(value or "").strip()
    if not text:
        return None
    direct = normalize_wikidata_qid(text)
    if direct:
        return direct
    match = _WIKIDATA_ENTITY_RE.search(text)
    if match:
        return normalize_wikidata_qid(match.group(1))
    return None


def map_local_name_to_wikidata_pid(local_name: str) -> str | None:
    name = ontology_local_name(local_name)
    if not name:
        return None
    pid = HMO_LOCAL_NAME_TO_WIKIDATA_PID.get(name, "")
    return pid if is_wikidata_pid(pid) else None


def map_class_local_name_to_wikidata_qid(local_name: str) -> str | None:
    name = ontology_local_name(local_name)
    if not name:
        return None
    qid = HMO_CLASS_TO_WIKIDATA_QID.get(name, "")
    return qid if is_wikidata_qid(qid) else None


def map_property_to_wikidata_pid(
    *,
    wikidata_property: str | None = None,
    property_uri: str | None = None,
    property_local_name: str | None = None,
    project_property_id: str | None = None,
    ontology_uri_by_project_pid: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve a claim predicate to a public Wikidata PID.

    Order:
    1. Explicit ``wikidata_property`` already on the claim
    2. Ontology URI / local-name allowlist
    3. Project Wikibase PID → ontology URI (schema ledger) → allowlist

    A bare project ``P…`` with no ontology URI never becomes a Wikidata PID.
    """
    explicit = str(wikidata_property or "").strip()
    if is_wikidata_pid(explicit):
        return explicit

    for candidate in (property_local_name, property_uri):
        mapped = map_local_name_to_wikidata_pid(str(candidate or ""))
        if mapped:
            return mapped

    project_pid = str(project_property_id or "").strip()
    if project_pid and ontology_uri_by_project_pid:
        uri = str(ontology_uri_by_project_pid.get(project_pid) or "").strip()
        mapped = map_local_name_to_wikidata_pid(uri)
        if mapped:
            return mapped
    return None


def map_item_value_to_wikidata_qid(
    *,
    wikidata_value: str | None = None,
    target_uri: str | None = None,
    target_qid: str | None = None,
    project_item_qid: str | None = None,
    explicit_wikidata_claim: bool = False,
) -> str | None:
    """Resolve an item-valued claim to a public Wikidata QID.

    Never treats a bare project Wikibase ``Q…`` as Wikidata (Rule W-83) unless
    the claim already carries an explicit ``wikidata_property`` / value that
    marks it as a public projection (``explicit_wikidata_claim=True``).
    """
    for candidate in (wikidata_value, target_uri):
        text = str(candidate or "").strip()
        if not text:
            continue
        qid = extract_wikidata_qid(text)
        if qid:
            return qid
        class_qid = map_class_local_name_to_wikidata_qid(text)
        if class_qid:
            return class_qid

    if explicit_wikidata_claim:
        for candidate in (wikidata_value, target_qid):
            qid = extract_wikidata_qid(candidate)
            if qid and qid != str(project_item_qid or "").strip():
                return qid
    return None


def claim_allowed_for_entity_type(property_id: str, entity_type: str) -> bool:
    """Entity-type gate after PID resolution (fail-closed for MS P50, etc.)."""
    pid = str(property_id or "").strip()
    et = str(entity_type or "").strip().lower()
    if not is_wikidata_pid(pid):
        return False
    if et == "manuscript" and pid in MANUSCRIPT_FORBIDDEN_WIKIDATA_PIDS:
        return False
    if pid == P_AUTHOR and et != "work":
        return False
    if pid in PERSON_ONLY_WIKIDATA_PIDS and et != "person":
        return False
    if pid in MANUSCRIPT_ONLY_WIKIDATA_PIDS and et != "manuscript":
        return False
    return True


def map_hmo_claim_to_wikidata(
    claim: Mapping[str, Any],
    *,
    entity_type: str = "",
    ontology_uri_by_project_pid: Mapping[str, str] | None = None,
    project_item_qid: str | None = None,
) -> MappedWikidataClaim | None:
    """Map one HMO canonical claim dict to a public Wikidata snak, or None."""
    property_id = map_property_to_wikidata_pid(
        wikidata_property=str(claim.get("wikidata_property") or "") or None,
        property_uri=str(claim.get("property_uri") or "") or None,
        property_local_name=str(claim.get("property_local_name") or "") or None,
        project_property_id=str(claim.get("property_id") or "") or None,
        ontology_uri_by_project_pid=ontology_uri_by_project_pid,
    )
    if not property_id or not claim_allowed_for_entity_type(property_id, entity_type):
        return None

    value_type = str(claim.get("value_type") or claim.get("datatype") or "").strip()
    is_item = (
        value_type in {"wikibase-item", "wikibase-entityid", "item"}
        or bool(claim.get("target_uri"))
        or bool(claim.get("target_qid"))
    )

    if is_item:
        explicit = is_wikidata_pid(str(claim.get("wikidata_property") or "").strip())
        scalar = ""
        raw_val = claim.get("value")
        if isinstance(raw_val, dict):
            scalar = str(raw_val.get("text") or raw_val.get("value") or "").strip()
        else:
            scalar = str(raw_val or "").strip()
        qid = map_item_value_to_wikidata_qid(
            wikidata_value=str(claim.get("wikidata_value") or scalar or "") or None,
            target_uri=str(claim.get("target_uri") or "") or None,
            target_qid=str(claim.get("target_qid") or "") or None,
            project_item_qid=project_item_qid,
            explicit_wikidata_claim=explicit,
        )
        if not qid:
            return None
        return MappedWikidataClaim(property_id=property_id, value=qid, value_type="wikibase-item")

    raw = claim.get("value")
    if isinstance(raw, dict):
        text = str(raw.get("text") or raw.get("value") or "").strip()
    else:
        text = str(raw or claim.get("wikidata_value") or "").strip()
    if not text:
        return None

    # external_wikidata_uri / exact-match URLs
    if property_id in {P_EXACT_MATCH, P_DESCRIBED_AT_URL, P_FULL_WORK_URL, P_IIIF_MANIFEST}:
        return MappedWikidataClaim(property_id=property_id, value=text, value_type="url")

    qid = extract_wikidata_qid(text)
    if qid and property_id == P_EXACT_MATCH:
        return MappedWikidataClaim(property_id=property_id, value=qid, value_type="wikibase-item")

    return MappedWikidataClaim(property_id=property_id, value=text, value_type="string")
