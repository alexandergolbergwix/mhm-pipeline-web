"""Export-time quality checks for HMO Wikibase entity drafts."""

from __future__ import annotations

import re
from dataclasses import dataclass

from converter.rdf.rdf_helpers import label_language_for_text
from converter.wikibase.models import WikibaseEntityDraft

_GENERIC_DESC_RE = re.compile(
    r"in the Hebrew Manuscripts Ontology \(HMO\)|in the Hebrew manuscripts corpus",
    re.IGNORECASE,
)
_IN_MS_LABEL_RE = re.compile(r"\(in MS\b", re.IGNORECASE)
_GERSHAYIM_RE = re.compile(r'(?<=[֐-ת])"(?=[֐-ת])')
_BARE_TIMESPAN_RE = re.compile(r"^\d{3,4}(-\d{3,4})?$")
_PRODUCTION_REPEATS_LABEL_RE = re.compile(
    r"^Production event for manuscript \S+\.?$", re.IGNORECASE
)
_PRIMARY_TYPES = frozenset({
    "E21_Person",
    "F1_Work",
    "F2_Expression",
    "F4_Manifestation_Singleton",
    "Manuscript",
})
_WITNESS_TYPES = frozenset({"TransmissionWitness", "TextTradition"})


def _has_unbalanced_quotes(label: str) -> bool:
    """True when a label carries ISBD quote/paren artifacts (gershayim-safe)."""
    without_gershayim = _GERSHAYIM_RE.sub("", label)
    if without_gershayim.count('"') % 2 == 1:
        return True
    if label.rstrip().endswith(")") and label.count(")") > label.count("("):
        return True
    return False


@dataclass(frozen=True)
class ExportQualityIssue:
    code: str
    local_id: str
    entity_type: str
    message: str


def audit_entity_draft(draft: WikibaseEntityDraft) -> list[ExportQualityIssue]:
    """Return quality issues for one exported entity draft."""
    issues: list[ExportQualityIssue] = []
    labels = draft.labels or {}
    descriptions = draft.descriptions or {}
    local_id = draft.local_id
    entity_type = draft.entity_type or ""

    if local_id.startswith("QDraft_BlankNode"):
        issues.append(ExportQualityIssue(
            code="blank_node_exported",
            local_id=local_id,
            entity_type=entity_type,
            message="Blank nodes must not be exported as Wikibase items",
        ))

    he = labels.get("he")
    en = labels.get("en")
    if he and en and he == en and label_language_for_text(he) == "he":
        issues.append(ExportQualityIssue(
            code="hebrew_label_in_en_slot",
            local_id=local_id,
            entity_type=entity_type,
            message="Hebrew text duplicated into the English label slot",
        ))

    for label in labels.values():
        if _IN_MS_LABEL_RE.search(label):
            issues.append(ExportQualityIssue(
                code="in_ms_label_noise",
                local_id=local_id,
                entity_type=entity_type,
                message="Label still carries '(in MS …)' catalog noise",
            ))
            break

    desc_en = descriptions.get("en", "")
    if _GENERIC_DESC_RE.search(desc_en):
        issues.append(ExportQualityIssue(
            code="generic_hmo_description",
            local_id=local_id,
            entity_type=entity_type,
            message="Description uses generic HMO fallback wording",
        ))

    if entity_type == "F1_Work" and desc_en.startswith("Literary work"):
        if "manuscript" not in desc_en and " MS " not in desc_en:
            issues.append(ExportQualityIssue(
                code="work_missing_manuscript_scope",
                local_id=local_id,
                entity_type=entity_type,
                message="Work description lacks manuscript scope",
            ))

    if entity_type in _PRIMARY_TYPES and not descriptions:
        issues.append(ExportQualityIssue(
            code="missing_description",
            local_id=local_id,
            entity_type=entity_type,
            message="Primary scholarly entity has no description",
        ))

    if entity_type in _PRIMARY_TYPES and not labels:
        issues.append(ExportQualityIssue(
            code="missing_label",
            local_id=local_id,
            entity_type=entity_type,
            message="Primary scholarly entity has no label",
        ))

    if entity_type == "E12_Production":
        if not en or not en.startswith("Production of MS"):
            issues.append(ExportQualityIssue(
                code="production_missing_label",
                local_id=local_id,
                entity_type=entity_type,
                message="Production event lacks a substantive English label",
            ))
        if _PRODUCTION_REPEATS_LABEL_RE.match(desc_en.strip()):
            issues.append(ExportQualityIssue(
                code="production_description_repeats_label",
                local_id=local_id,
                entity_type=entity_type,
                message="Production description merely repeats the label template",
            ))

    if entity_type == "E52_Time-Span":
        for label in labels.values():
            if _BARE_TIMESPAN_RE.match(label.strip()):
                issues.append(ExportQualityIssue(
                    code="timespan_bare_label",
                    local_id=local_id,
                    entity_type=entity_type,
                    message="Time-Span label is a bare year with no context",
                ))
                break

    if he and label_language_for_text(he) == "en":
        issues.append(ExportQualityIssue(
            code="latin_label_in_he",
            local_id=local_id,
            entity_type=entity_type,
            message="Latin/English text tagged as a Hebrew (he) label",
        ))

    for label in labels.values():
        if _has_unbalanced_quotes(label):
            issues.append(ExportQualityIssue(
                code="unbalanced_label_quotes",
                local_id=local_id,
                entity_type=entity_type,
                message="Label has unbalanced quotes or a dangling parenthesis",
            ))
            break

    if entity_type in _WITNESS_TYPES and not labels:
        issues.append(ExportQualityIssue(
            code="witness_unusable_title",
            local_id=local_id,
            entity_type=entity_type,
            message="Transmission witness / tradition has no usable title label",
        ))

    return issues


def audit_entity_drafts(drafts: list[WikibaseEntityDraft]) -> list[ExportQualityIssue]:
    """Audit all exported drafts; return every issue found."""
    out: list[ExportQualityIssue] = []
    for draft in drafts:
        out.extend(audit_entity_draft(draft))
    return out
