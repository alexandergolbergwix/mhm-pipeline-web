"""P1574 work-link specificity ladder (corpus-scale, no CN allowlists).

Prefer a specific attested work QID over whole-Bible / Tanakh / unknown-text.
Detectors are pattern functions over title + notes + subjects.
"""

from __future__ import annotations

import re
from typing import Any

# Live-verified 2026-08-08 via wbsearchentities + wbgetentities (Rule W-26).
Q_BIBLE = "Q1845"
Q_TANAKH = "Q83367"
Q_BOOK_OF_ESTHER = "Q131068"  # Book of Esther
Q_UNKNOWN_TEXT = "Q234460"

BROAD_SCRIPTURE_QIDS: frozenset[str] = frozenset({Q_BIBLE, Q_TANAKH})

# Megillah / biblical-book detectors → specific work QIDs (live-verified).
_MEGILLAH_ESTHER_RE = re.compile(
    r"מגילת\s*אסתר|megillat\s+esther|book\s+of\s+esther|esther\s+scroll|"
    r"מגילה\s+של\s+אסתר|מגילת\-אסתר",
    re.IGNORECASE,
)

_PIYYUT_OR_MISC_RE = re.compile(
    r"פיוט|פיוטים|piyyut|liturgical\s+poetr|miscellan|"
    r"קובץ\s+פיוט|שירי\s+קודש|סדר\s+פיוט",
    re.IGNORECASE,
)

_WHOLE_BIBLE_ATTEST_RE = re.compile(
    r"(?:^|[\s,;])(?:תנ[\"״]?ך|tanakh|tanach|hebrew\s+bible|(?:the\s+)?bible|"
    r"complete\s+bible|entire\s+bible|מקרא\s+שלם)(?:$|[\s,;.])",
    re.IGNORECASE,
)


def record_evidence_text(record: dict[str, Any] | None) -> str:
    """Concatenate title / notes / subjects for pattern detectors."""
    if not record:
        return ""
    chunks: list[str] = []
    for key in (
        "title", "subtitle", "summary", "notes", "colophon_text",
        "245$a", "245$b", "500$a", "505$a",
    ):
        value = record.get(key)
        if isinstance(value, list):
            chunks.extend(str(v) for v in value if v)
        elif value:
            chunks.append(str(value))
    for subject in record.get("subjects") or []:
        if isinstance(subject, dict):
            chunks.append(str(subject.get("term") or subject.get("name") or ""))
        else:
            chunks.append(str(subject or ""))
    for genre in list(record.get("genres") or []) + list(record.get("genre_entries") or []):
        if isinstance(genre, dict):
            chunks.append(str(genre.get("term") or genre.get("name") or ""))
        else:
            chunks.append(str(genre or ""))
    return " ".join(chunks)


def specific_biblical_work_qid(text: str) -> str | None:
    """Return a book-level QID when *text* names a specific biblical book."""
    blob = str(text or "")
    if _MEGILLAH_ESTHER_RE.search(blob):
        return Q_BOOK_OF_ESTHER
    return None


def blocks_broad_scripture_qid(record: dict[str, Any] | None, title: str = "") -> bool:
    """True when piyyut / miscellany evidence forbids whole-Bible / Tanakh P1574."""
    record_blob = record_evidence_text(record)
    # RELATED_WORKS often supplies a bare "Bible"/"תנ״ך" *candidate* title.
    # That alias must not cancel a piyyut manuscript's block (export-34 / W-173).
    if _PIYYUT_OR_MISC_RE.search(record_blob) and not _WHOLE_BIBLE_ATTEST_RE.search(
        record_blob,
    ):
        return True
    blob = f"{title} {record_blob}"
    if specific_biblical_work_qid(blob):
        return True
    if _PIYYUT_OR_MISC_RE.search(blob) and not _WHOLE_BIBLE_ATTEST_RE.search(blob):
        return True
    return False


def whole_scripture_attested(record: dict[str, Any] | None, title: str = "") -> bool:
    """True when evidence claims the collection as a whole (not one book).

    Attestation must come from the manuscript record (title/notes/genres), not
    solely from a RELATED_WORKS alias equal to ``Bible`` / ``תנ״ך``.
    """
    record_blob = record_evidence_text(record)
    if specific_biblical_work_qid(f"{title} {record_blob}"):
        return False
    if _WHOLE_BIBLE_ATTEST_RE.search(record_blob):
        return True
    # Manuscript's own title may attest (e.g. ``תנ״ך`` as 245$a), but a bare
    # known-work alias passed as *title* without record support does not.
    ms_title = ""
    if record:
        ms_title = str(record.get("title") or record.get("245$a") or "")
    return bool(_WHOLE_BIBLE_ATTEST_RE.search(ms_title))


def refine_exemplar_work_qid(
    work_qid: str | None,
    *,
    title: str = "",
    record: dict[str, Any] | None = None,
) -> str | None:
    """Apply the specificity ladder to a candidate P1574 QID.

    1. Prefer a specific biblical book detected in title/notes.
    2. Keep non-broad QIDs unchanged.
    3. Keep Bible/Tanakh only when the collection as a whole is attested.
    4. Otherwise drop the broad QID (caller may CREATE local / omit).
    """
    blob = f"{title} {record_evidence_text(record)}"
    specific = specific_biblical_work_qid(blob)
    if specific:
        return specific

    qid = str(work_qid or "").strip()
    if not qid:
        return None
    if qid not in BROAD_SCRIPTURE_QIDS:
        return qid
    if blocks_broad_scripture_qid(record, title):
        return None
    if whole_scripture_attested(record, title):
        return qid
    return None


def should_emit_unknown_text_exemplar(
    *,
    catalog_title: str,
    other_exemplar_qids: set[str] | None = None,
) -> bool:
    """Refuse bare unknown-text when specific exemplars already exist."""
    title = str(catalog_title or "").strip()
    if not title:
        return False
    others = {str(q) for q in (other_exemplar_qids or set()) if q}
    others.discard(Q_UNKNOWN_TEXT)
    if any(not q.startswith("__LOCAL:") for q in others):
        return False
    if any(q.startswith("__LOCAL:") for q in others):
        return False
    return True
