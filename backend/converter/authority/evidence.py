"""Fail-closed authority evidence shared by HMO and Wikidata projections.

This module deliberately does not perform network lookups.  It normalises
identifiers already present in the RDF graph and refuses to promote
conflicting identifiers to an accepted authority claim.  Matchers may attach
their raw candidates as evidence and run this gate before writing claims.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable

_WIKIDATA_QID_RE = re.compile(r"^Q[1-9][0-9]*$", re.IGNORECASE)
_VIAF_ID_RE = re.compile(r"^[0-9]{4,15}$")
_AUTHORITY_ID_RE = re.compile(r"^[1-9][0-9]{0,19}$")
_MAZAL_ID_RE = re.compile(r"^987[0-9]{6,17}$")


@dataclass(frozen=True)
class AuthorityEvidence:
    """One candidate identifier and the evidence that produced it."""

    source: str
    identifier: str
    kind: str
    accepted: bool
    reason: str = ""
    details: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "source": self.source,
            "identifier": self.identifier,
            "kind": self.kind,
            "accepted": self.accepted,
        }
        if self.reason:
            result["reason"] = self.reason
        if self.details:
            result["details"] = dict(self.details)
        return result


def normalize_wikidata_qid(value: object) -> str | None:
    """Return a canonical Wikidata QID, or ``None`` for unsafe input."""
    text = str(value or "").strip().rstrip("/").split("/")[-1]
    return text.upper() if _WIKIDATA_QID_RE.fullmatch(text) else None


def normalize_viaf_id(value: object) -> str | None:
    """Return the numeric VIAF cluster id from an id or URI."""
    text = str(value or "").strip().rstrip("/")
    if "/viaf/" in text.lower():
        text = text.rsplit("/", 1)[-1]
    # 987007… values are NLI/Mazal authority identifiers, not VIAF clusters.
    if text.startswith("987007"):
        return None
    return text if _VIAF_ID_RE.fullmatch(text) else None


def normalize_authority_id(value: object) -> str | None:
    """Return a positive numeric local authority identifier."""
    text = str(value or "").strip().rstrip("/").rsplit("/", 1)[-1]
    return text if _AUTHORITY_ID_RE.fullmatch(text) else None


def evidence_row_ids(row: object) -> dict[str, str]:
    """Collect viaf/mazal/wikidata ids from HMO or legacy person evidence.

    HMO rows use ``kind`` + ``identifier``/``value``. Legacy
    ``person_projection`` rows use ``viaf_uri``/``mazal_id``/``wikidata_qid``
    without ``kind``. A combined row may yield more than one kind.
    """
    if not isinstance(row, dict):
        return {}
    kind = str(row.get("kind") or "").strip().lower()
    out: dict[str, str] = {}

    def _first(*keys: str) -> str:
        for key in keys:
            text = str(row.get(key) or "").strip()
            if text:
                return text
        return ""

    wiki = _first("wikidata_qid")
    if kind == "wikidata":
        wiki = wiki or _first("identifier", "value")
    if wiki:
        out["wikidata"] = wiki

    viaf = _first("viaf_id", "viaf_uri")
    if kind == "viaf":
        viaf = viaf or _first("identifier", "value")
    if viaf:
        out["viaf"] = viaf

    mazal = _first("mazal_id")
    if kind == "mazal":
        mazal = mazal or _first("identifier", "value")
    if mazal:
        out["mazal"] = mazal

    return out


def gate_candidates(candidates: Iterable[AuthorityEvidence]) -> list[AuthorityEvidence]:
    """Mark only unambiguous candidates as accepted.

    A source may emit duplicate forms (a QID claim and its sameAs URI); those
    collapse to one identifier.  If one authority kind has multiple distinct
    identifiers, every candidate of that kind is retained as evidence but
    marked ``accepted=False``.
    """
    unique: dict[tuple[str, str], AuthorityEvidence] = {}
    for candidate in candidates:
        if candidate.identifier:
            unique.setdefault((candidate.kind, candidate.identifier), candidate)
    grouped: dict[str, list[AuthorityEvidence]] = {}
    for candidate in unique.values():
        grouped.setdefault(candidate.kind, []).append(candidate)

    output: list[AuthorityEvidence] = []
    for kind, values in grouped.items():
        ambiguous = len(values) > 1
        reason = "conflicting candidates" if ambiguous else ""
        for value in values:
            output.append(
                AuthorityEvidence(
                    source=value.source,
                    identifier=value.identifier,
                    kind=kind,
                    accepted=value.accepted and not ambiguous,
                    reason=reason or value.reason,
                    details=value.details,
                )
            )
    return sorted(output, key=lambda item: (item.kind, item.identifier, item.source))


def evidence_from_values(
    values: Iterable[tuple[str, object]],
) -> list[AuthorityEvidence]:
    """Normalise common RDF authority values and apply the ambiguity gate."""
    candidates: list[AuthorityEvidence] = []
    for source, raw in values:
        qid = normalize_wikidata_qid(raw)
        if qid:
            candidates.append(AuthorityEvidence(source, qid, "wikidata", True))
            continue
        viaf = normalize_viaf_id(raw)
        if viaf and ("viaf" in source.lower() or "sameas" in source.lower()):
            candidates.append(AuthorityEvidence(source, viaf, "viaf", True))
            continue

        source_key = source.lower().replace("-", "_")
        authority_id = normalize_authority_id(raw)
        if authority_id and "kima" in source_key:
            candidates.append(AuthorityEvidence(source, authority_id, "kima", True))
            continue
        if authority_id and "mazal" in source_key:
            candidates.append(AuthorityEvidence(source, authority_id, "mazal", True))
            continue
        if authority_id and source_key in {"external_uri_nli", "authority_id"}:
            # GraphBuilder historically stores Mazal references in these
            # generic fields.  The 987... namespace is the only safe
            # discriminator; arbitrary NLI/control numbers must abstain.
            if _MAZAL_ID_RE.fullmatch(authority_id):
                candidates.append(AuthorityEvidence(source, authority_id, "mazal", True))
    return gate_candidates(candidates)


__all__ = [
    "AuthorityEvidence",
    "evidence_from_values",
    "gate_candidates",
    "normalize_viaf_id",
    "normalize_wikidata_qid",
]
