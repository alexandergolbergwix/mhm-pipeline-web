"""Source-aware eligibility checks for projected Wikidata work items."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from converter.rdf.rdf_helpers import (
    is_descriptive_content_title,
    sanitize_work_title,
)

_HEBREW_RE = re.compile(r"[\u0590-\u05ff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_BIBLIOGRAPHIC_FRAGMENT_RE = re.compile(
    r"(?:מהדורת|במהדורה|ירושלים|תל[־ -]?אביב|דפוס|עמ(?:וד)?[\s׳'\"]|"
    r"סי(?:מן)?[\s׳'\"]|חלק\s+[א-ת]|כרך\s+[א-ת]|folio|edition|vol\.?\s|pp?\.?\s)",
    re.IGNORECASE,
)
_PROSE_PREFIX_RE = re.compile(
    r"^(?:החכם|המחבר|הכותב|הנזכר|נדפס|נעתק|קטע(?:ים)?\s+מ|התחלה|סוף|"
    r"רשימה|תיאור|על\s+אודות|מתוך\s+כתב|רק\s|פחות\s|בין\s+השאר|"
    r"מערב\s+אירופה|לכבוד\s|הדוכס\s|השר(?:ית|\s)|הר[\"״]?ר\s)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WorkCandidateDecision:
    """A compact, serialisable explanation of a work-candidate decision."""

    title: str
    source_field: str
    accepted: bool
    reason: str
    raw_title: str = ""
    folio_range: str = ""
    sequence: int | None = None
    source_text: str = ""

    def evidence(self) -> dict[str, object]:
        return asdict(self)


def assess_work_candidate(
    title: object,
    *,
    source_field: object,
    approved: bool = False,
    known_qid: str | None = None,
    candidate_kind: object = "",
    folio_range: object = "",
    sequence: object = None,
    source_text: object = "",
) -> WorkCandidateDecision:
    """Decide whether source evidence identifies a public work entity."""
    raw_title = str(title or "").strip()
    cleaned = sanitize_work_title(raw_title).rstrip(" .,;:/-")
    source = str(source_field or "").strip().upper()
    kind = str(candidate_kind or "").strip().casefold()
    folio = str(folio_range or "").strip()
    try:
        seq = int(sequence) if sequence is not None else None
    except (TypeError, ValueError):
        seq = None

    def decision(accepted: bool, reason: str) -> WorkCandidateDecision:
        return WorkCandidateDecision(
            title=cleaned,
            source_field=source,
            accepted=accepted,
            reason=reason,
            raw_title=raw_title,
            folio_range=folio,
            sequence=seq,
            source_text=str(source_text or "").strip(),
        )

    if not cleaned or len(re.sub(r"[^\w\u0590-\u05ff]", "", cleaned)) < 3:
        return decision(False, "empty_or_too_short")
    if is_descriptive_content_title(cleaned):
        return decision(False, "descriptive_note")
    if _BIBLIOGRAPHIC_FRAGMENT_RE.search(cleaned):
        return decision(False, "bibliographic_fragment")
    if _PROSE_PREFIX_RE.search(cleaned):
        return decision(False, "catalogue_prose")
    if known_qid:
        return decision(True, "known_wikidata_work")
    if approved:
        return decision(True, "curator_approved_work")
    if _LATIN_RE.search(cleaned) and not _HEBREW_RE.search(cleaned):
        return decision(False, "latin_title_requires_authority")
    if source == "500":
        if kind != "named_work":
            return decision(False, "unstructured_500_note")
        return decision(True, "named_work_in_500")
    if source == "505":
        return decision(True, "named_work_in_505")
    if source == "CONTENTS_NER":
        return decision(False, "unapproved_ner_work")
    return decision(False, "unsupported_source")
