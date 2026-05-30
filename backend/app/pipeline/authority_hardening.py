"""Authority-matching hardening guards (pure helpers).

This module ports the seven hardening guards the desktop pipeline runs
inside ``AuthorityWorker._match_marc_person_entry`` (in
``src/mhm_pipeline/controller/workers.py``) into the web backend as a
set of small, pure functions. Each guard takes a candidate match
dict (and whatever context it needs) and returns a :class:`GuardVerdict`
describing whether it fired, what to downgrade the confidence to, and
why.

The seven guards are:

1. ``guard_short_name_homonym`` — single-token MARC names that land on
   a richly-disambiguated cluster without independent corroboration.
2. ``guard_placeholder_name`` — cataloguer abbreviations like ``א״א``,
   ``N.N.``, ``Anonymous`` are not real persons.
3. ``guard_cluster_collapse`` — two distinct names in the same record
   that resolved to the same VIAF cluster.
4. ``guard_nli_strict_skip_viaf`` — when Mazal has a hit the desktop
   skips the VIAF SRU search entirely. Web matcher already calls
   every backend, so this guard fires when both sources resolved and
   the Mazal verdict should anchor the confidence.
5. ``guard_wikidata_crosscheck`` — query Wikidata for the VIAF cluster
   and flag disagreement / confirmation / over-merge.
6. ``guard_mazal_pair_collision`` — two distinct (marc_name, mazal_id)
   tuples in the same record share a VIAF cluster.
7. ``guard_corporate_meeting`` — organisations / meetings must skip
   the person-name VIAF search (cross-type cluster contamination).

Design notes
------------

The desktop's :mod:`converter.authority.stage3_guards` module is
already byte-identical in this repo. The date-conflict guard
(``evaluate_date_conflict``) is wired into :mod:`app.pipeline.authority`
directly; we don't duplicate it here. ``is_placeholder_name`` and the
short-name detector live in ``stage3_guards``; we re-export them as
guards so the web caller sees a uniform :class:`GuardVerdict` shape.

Each guard is pure (no DB, no HTTP unless explicitly invoked through
the desktop-provided helpers, no Qt). When the data a guard needs is
not available on the existing match payload (e.g. the per-record
OverMergeTable doesn't exist in the web flow because matches are
persisted asynchronously) the guard returns ``fired=False`` with a
descriptive ``reason``.

The web orchestrator is responsible for accumulating fired guards,
downgrading the confidence to the lowest target, and stamping
``payload["guard_flags"]``. See :func:`apply_hardening_guards`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Result type ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GuardVerdict:
    """The result of running a single guard.

    Attributes:
        fired: True if the guard's condition matched.
        new_confidence: When ``fired`` is True, the confidence bucket
            the caller SHOULD apply if it is lower than the current
            value. ``None`` keeps the current bucket (informational
            flags use this for ``wikidata_confirms``).
        reason: Human-readable explanation. Empty when ``fired=False``.
        flag: Stable string flag name (e.g. ``"short_name_homonym"``).
            Empty when ``fired=False``.
    """

    fired: bool
    new_confidence: str | None = None
    reason: str = ""
    flag: str = ""


_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


def _lower_confidence(current: str, target: str | None) -> str:
    """Return whichever of *current* / *target* is the lower bucket.

    ``None`` target keeps *current*. Unknown values fall back to
    *current* defensively.
    """
    if not target:
        return current
    cur = _CONFIDENCE_ORDER.get(current, _CONFIDENCE_ORDER["medium"])
    tgt = _CONFIDENCE_ORDER.get(target, _CONFIDENCE_ORDER["medium"])
    return target if tgt < cur else current


# ── Guard 1 — short-name homonym ────────────────────────────────────────


def guard_short_name_homonym(
    *,
    marc_name: str,
    preferred_name_lat: str | None,
    mazal_matched: bool,
    biographical_dates_present: bool,
) -> GuardVerdict:
    """Single-token Hebrew name lands on a richly-disambiguated cluster.

    Mirrors :func:`converter.authority.stage3_guards.is_short_name_homonym`.
    """
    try:
        from converter.authority.stage3_guards import (  # noqa: PLC0415
            is_short_name_homonym,
        )
    except Exception:  # noqa: BLE001 — defensive import
        logger.debug("stage3_guards.is_short_name_homonym unavailable", exc_info=True)
        return GuardVerdict(fired=False)

    fired = is_short_name_homonym(
        marc_name=marc_name,
        preferred_name_lat=preferred_name_lat,
        mazal_matched=mazal_matched,
        biographical_dates_present=biographical_dates_present,
    )
    if not fired:
        return GuardVerdict(fired=False)
    return GuardVerdict(
        fired=True,
        new_confidence="low",
        reason=(
            f"Single-token MARC name {marc_name!r} matched a richly-named "
            "cluster without independent corroboration."
        ),
        flag="short_name_homonym",
    )


# ── Guard 2 — placeholder name ──────────────────────────────────────────


def guard_placeholder_name(*, name: str) -> GuardVerdict:
    """Cataloguer placeholders / abbreviations.

    Mirrors :func:`converter.authority.stage3_guards.is_placeholder_name`.
    When this fires the caller should ALSO clear ``mazal_id`` / ``viaf_id``
    / ``wikidata_qid`` on the payload — the underlying match cannot be
    real.
    """
    try:
        from converter.authority.stage3_guards import (  # noqa: PLC0415
            is_placeholder_name,
        )
    except Exception:  # noqa: BLE001
        logger.debug("stage3_guards.is_placeholder_name unavailable", exc_info=True)
        return GuardVerdict(fired=False)

    if not is_placeholder_name(name):
        return GuardVerdict(fired=False)
    return GuardVerdict(
        fired=True,
        new_confidence="low",
        reason=(
            f"Name {name!r} is a cataloguer placeholder / abbreviation, "
            "not a real authority entry."
        ),
        flag="placeholder_name",
    )


# ── Guard 3 — cluster collapse ──────────────────────────────────────────


def guard_cluster_collapse(
    *,
    candidate: dict[str, Any],
    siblings: Sequence[dict[str, Any]],
) -> GuardVerdict:
    """Two distinct MARC names in the same record share a VIAF cluster.

    *candidate* is the current row; *siblings* is every OTHER match
    for the same control number (the caller filters self out).

    The desktop's post-pass (``stage3_guards.apply_cluster_collapse``)
    inspects the full list and downgrades both ends of the collision.
    Here we run the per-candidate side: report ``fired=True`` when the
    candidate's VIAF cluster appears on at least one sibling with a
    different normalised name.
    """
    viaf_id = _viaf_id_from(candidate)
    if not viaf_id:
        return GuardVerdict(fired=False)

    my_name = _normalise_name(candidate.get("matched_name") or candidate.get("entity_text") or "")
    if not my_name:
        return GuardVerdict(fired=False)

    for sibling in siblings:
        sib_viaf = _viaf_id_from(sibling)
        if not sib_viaf or sib_viaf != viaf_id:
            continue
        sib_name = _normalise_name(
            sibling.get("matched_name") or sibling.get("entity_text") or "",
        )
        if sib_name and sib_name != my_name:
            return GuardVerdict(
                fired=True,
                new_confidence="low",
                reason=(
                    f"VIAF cluster {viaf_id} matched two distinct names in this "
                    f"record ({my_name!r} and {sib_name!r}) — cluster collapse."
                ),
                flag="cluster_collapse",
            )
    return GuardVerdict(fired=False)


# ── Guard 4 — NLI-strict mode (F4) ──────────────────────────────────────


def guard_nli_strict_skip_viaf(
    *,
    candidate: dict[str, Any],
) -> GuardVerdict:
    """When Mazal returned a hit, the desktop skips VIAF SRU search.

    The web matcher already calls every backend, so by the time we run
    this guard both VIAF and Mazal may be present. We fire when Mazal
    AND VIAF disagree about a Wikidata QID anchor — that's the signal
    that VIAF SRU drifted (the same problem F4 was designed to dodge).

    We CANNOT re-run the matcher here. The guard is a low-cost
    after-the-fact check: if the candidate's payload carries both
    ``mazal_id`` and ``viaf_id`` but the resolved ``wikidata_qid`` is
    suspiciously high (≥ Q138_000_000, pipeline-created duplicate
    range), prefer Mazal and demote.
    """
    payload = candidate.get("payload") or {}
    mazal_id = candidate.get("mazal_id") or payload.get("mazal_id")
    viaf_id = candidate.get("viaf_id") or payload.get("viaf_id")
    wikidata_qid = candidate.get("wikidata_qid") or payload.get("wikidata_qid")

    if not (mazal_id and viaf_id):
        return GuardVerdict(fired=False)
    if not wikidata_qid or not str(wikidata_qid).startswith("Q"):
        return GuardVerdict(fired=False)
    try:
        qid_num = int(str(wikidata_qid)[1:])
    except (ValueError, IndexError):
        return GuardVerdict(fired=False)

    if qid_num < 138_000_000:
        return GuardVerdict(fired=False)

    return GuardVerdict(
        fired=True,
        new_confidence="medium",
        reason=(
            f"Mazal + VIAF agreed but resolved to pipeline-range QID "
            f"{wikidata_qid} (≥ Q138_000_000) — prefer Mazal anchor."
        ),
        flag="nli_strict_skip_viaf",
    )


# ── Guard 5 — Wikidata cross-check (F2) ─────────────────────────────────


def guard_wikidata_crosscheck(
    *,
    marc_name: str,
    candidate: dict[str, Any],
    over_merge_table: Any | None = None,
) -> GuardVerdict:
    """Query Wikidata for the VIAF cluster's QIDs and Hebrew labels.

    Mirrors the inline block in
    ``AuthorityWorker._match_marc_person_entry`` (search for
    ``wikidata_crosscheck``). Disabled by default in the web flow
    unless ``MHM_DISABLE_WIKIDATA_CROSSCHECK=0`` — we ship the
    guard but the caller controls live network access.

    The guard returns ``fired=True`` for either disagreement or
    over-merge. Confirmation is also reported back (via the verdict's
    ``flag``) but kept ``fired=False`` so it does not downgrade.
    """
    viaf_id = _viaf_id_from(candidate)
    if not viaf_id:
        return GuardVerdict(fired=False)

    try:
        from converter.authority.wikidata_crosscheck import (  # noqa: PLC0415
            hebrew_label_matches,
            is_enabled,
            is_overmerged,
            lookup_viaf,
        )
    except Exception:  # noqa: BLE001
        logger.debug("wikidata_crosscheck module unavailable", exc_info=True)
        return GuardVerdict(fired=False)

    if not is_enabled():
        return GuardVerdict(fired=False)

    try:
        if over_merge_table is not None:
            wd_result = over_merge_table.get(viaf_id)
        else:
            wd_result = lookup_viaf(viaf_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Wikidata cross-check raised for VIAF %s: %s", viaf_id, exc)
        return GuardVerdict(fired=False)

    if wd_result is None or getattr(wd_result, "error", None) is not None:
        return GuardVerdict(fired=False)

    if is_overmerged(wd_result):
        return GuardVerdict(
            fired=True,
            new_confidence="low",
            reason=(
                f"VIAF cluster {viaf_id} is over-merged on Wikidata "
                f"({len(wd_result.qids)} distinct items with disagreeing dates "
                "or occupations)."
            ),
            flag="wikidata_crosscheck_fail",
        )

    if wd_result.qids and not hebrew_label_matches(marc_name, wd_result.hebrew_labels):
        return GuardVerdict(
            fired=True,
            new_confidence="medium",
            reason=(
                f"VIAF cluster {viaf_id} has Wikidata items but none of their "
                f"Hebrew labels match {marc_name!r} within edit distance ≤ 2."
            ),
            flag="wikidata_crosscheck_fail",
        )

    return GuardVerdict(fired=False)


# ── Guard 6 — Mazal-pair collision (F3) ─────────────────────────────────


def guard_mazal_pair_collision(
    *,
    candidate: dict[str, Any],
    siblings: Sequence[dict[str, Any]],
) -> GuardVerdict:
    """Two distinct (marc_name, mazal_id) pairs sharing one VIAF cluster.

    The desktop drives this via a per-run :class:`OverMergeTable`. The
    web flow has no such cross-record bookkeeping, so we apply it
    per-record: scan the sibling matches in the same control number
    and fire when the same VIAF id was reached from a different
    (marc_name, mazal_id) pair.
    """
    my_viaf = _viaf_id_from(candidate)
    my_mazal = (candidate.get("mazal_id") or "").strip()
    my_name = _normalise_name(candidate.get("matched_name") or candidate.get("entity_text") or "")
    if not (my_viaf and my_mazal and my_name):
        return GuardVerdict(fired=False)

    for sibling in siblings:
        sib_viaf = _viaf_id_from(sibling)
        if sib_viaf != my_viaf:
            continue
        sib_mazal = (sibling.get("mazal_id") or "").strip()
        sib_name = _normalise_name(
            sibling.get("matched_name") or sibling.get("entity_text") or "",
        )
        if not (sib_mazal and sib_name):
            continue
        if sib_mazal != my_mazal and sib_name != my_name:
            return GuardVerdict(
                fired=True,
                new_confidence="low",
                reason=(
                    f"VIAF cluster {my_viaf} was reached from two distinct "
                    f"(name, mazal_id) pairs in this record — over-merge."
                ),
                flag="mazal_pair_collision",
            )
    return GuardVerdict(fired=False)


# ── Guard 7 — corporate / meeting routing ───────────────────────────────


_CORPORATE_KIND_VALUES = frozenset({"organization", "organisation", "corporate", "meeting"})


def guard_corporate_meeting(
    *,
    candidate: dict[str, Any],
    entity_kind: str | None = None,
) -> GuardVerdict:
    """Corporate / meeting entities must not carry a person-VIAF id.

    Desktop's :meth:`AuthorityWorker._match_against_authorities` returns
    ``(None, None)`` for ``entity_type in ("organization", "meeting")``
    so VIAF SRU never runs. Web matcher already routed those calls but
    we still need to defensively drop a VIAF id that crept onto an
    organisation row (e.g. an institution mis-classified as a person at
    Stage 2 and corrected later).
    """
    kind = (entity_kind or candidate.get("entity_kind") or "").lower()
    if kind not in _CORPORATE_KIND_VALUES:
        # Optional fallback — keyword check on the name (defensive only).
        name = candidate.get("matched_name") or candidate.get("entity_text") or ""
        if name and _looks_institutional(str(name)):
            kind = "organization"
        else:
            return GuardVerdict(fired=False)

    if not candidate.get("viaf_id"):
        return GuardVerdict(fired=False)

    return GuardVerdict(
        fired=True,
        new_confidence="low",
        reason=(
            f"Entity kind {kind!r} but a person-style VIAF id "
            f"{candidate.get('viaf_id')!r} is attached — VIAF SRU "
            "matches the wrong name type for organisations / meetings."
        ),
        flag="corporate_viaf_drop",
    )


def _looks_institutional(name: str) -> bool:
    """Cheap keyword check shared with the desktop item_builder."""
    try:
        from converter.wikidata.item_builder import (  # noqa: PLC0415
            is_institutional_name,
        )
    except Exception:  # noqa: BLE001
        return False
    try:
        return bool(is_institutional_name(name))
    except Exception:  # noqa: BLE001
        return False


# ── Shared helpers ──────────────────────────────────────────────────────


_VIAF_ID_RE = re.compile(r"/viaf/(\d+)")


def _viaf_id_from(candidate: dict[str, Any]) -> str:
    """Pull the numeric VIAF id off a candidate row.

    Accepts either ``viaf_id`` (bare digits, web shape) or a payload
    URI like ``"https://viaf.org/viaf/12345"`` (desktop shape).
    """
    direct = (candidate.get("viaf_id") or "").strip()
    if direct.isdigit():
        return direct
    if direct:
        match = _VIAF_ID_RE.search(direct)
        if match:
            return match.group(1)
    payload = candidate.get("payload") or {}
    uri = (payload.get("viaf_uri") or payload.get("viaf_id") or "").strip()
    if uri.isdigit():
        return uri
    if uri:
        match = _VIAF_ID_RE.search(uri)
        if match:
            return match.group(1)
    return ""


def _normalise_name(name: str) -> str:
    """Strip MARC trailing punctuation + collapse whitespace."""
    return " ".join(name.strip().rstrip(",;:.").split()).casefold()


# ── Orchestrator ────────────────────────────────────────────────────────


@dataclass
class HardeningContext:
    """Per-record state passed to the orchestrator.

    Attributes:
        siblings: Every OTHER candidate match in the same MARC record.
            Used by the cross-row guards (cluster collapse, mazal-pair
            collision). Pass ``[]`` when running on a single isolated
            candidate.
        preferred_name_lat: Latin preferred-name form from the cluster.
            Used by short-name homonym.
        biographical_dates_in_marc: True iff MARC 100$d / 700$d carried
            a dates subfield. Short-name homonym suppresses when True.
        entity_kind: ``"person"`` / ``"organization"`` / ``"meeting"``.
        over_merge_table: Optional caller-owned cache for repeated
            VIAF→Wikidata SPARQL hits. Web pipeline doesn't keep one
            per run; passing ``None`` causes a direct ``lookup_viaf``.
        enable_wikidata_crosscheck: When False, the Wikidata cross-check
            guard is skipped (default — web flow defers network calls).
    """

    siblings: Sequence[dict[str, Any]] = field(default_factory=list)
    preferred_name_lat: str | None = None
    biographical_dates_in_marc: bool = False
    entity_kind: str = "person"
    over_merge_table: Any | None = None
    enable_wikidata_crosscheck: bool = False


def apply_hardening_guards(
    candidate: dict[str, Any],
    *,
    context: HardeningContext | None = None,
) -> dict[str, Any]:
    """Run every guard on a candidate and return the updated row.

    The function:

    * runs the seven hardening guards (date-conflict is wired into
      :mod:`app.pipeline.authority` directly and not re-run here),
    * accumulates fired guard flags into ``payload["guard_flags"]``,
    * downgrades ``candidate["confidence"]`` to the lowest target
      bucket among the fired guards, and
    * appends one-line reasons to ``payload["reasoning"]``.

    The candidate dict is COPIED before mutation; the caller swaps in
    the returned dict. ``payload["guard_flags"]`` is preserved across
    calls — re-running the orchestrator is idempotent (duplicates are
    deduped).
    """
    ctx = context or HardeningContext()
    out = dict(candidate)
    payload = dict(out.get("payload") or {})
    existing_flags = list(payload.get("guard_flags") or [])

    matched_name = (
        out.get("matched_name") or out.get("entity_text") or ""
    )

    verdicts: list[GuardVerdict] = [
        guard_placeholder_name(name=str(matched_name)),
        guard_short_name_homonym(
            marc_name=str(matched_name),
            preferred_name_lat=ctx.preferred_name_lat,
            mazal_matched=bool(out.get("mazal_id")),
            biographical_dates_present=ctx.biographical_dates_in_marc,
        ),
        guard_cluster_collapse(candidate=out, siblings=ctx.siblings),
        guard_nli_strict_skip_viaf(candidate=out),
        guard_mazal_pair_collision(candidate=out, siblings=ctx.siblings),
        guard_corporate_meeting(candidate=out, entity_kind=ctx.entity_kind),
    ]
    if ctx.enable_wikidata_crosscheck:
        verdicts.append(
            guard_wikidata_crosscheck(
                marc_name=str(matched_name),
                candidate=out,
                over_merge_table=ctx.over_merge_table,
            ),
        )

    fired = [v for v in verdicts if v.fired]
    confidence = str(out.get("confidence") or "low")
    reasons: list[str] = []
    new_flags: list[str] = list(existing_flags)
    for v in fired:
        confidence = _lower_confidence(confidence, v.new_confidence)
        if v.flag and v.flag not in new_flags:
            new_flags.append(v.flag)
        if v.reason:
            reasons.append(f"⚠ {v.reason}")

    # Placeholder hard-rejects: clear the resolved IDs too. Matches the
    # desktop ``evaluate_match`` path which returns mazal_id=None /
    # viaf_uri=None when ``is_placeholder_name`` fires.
    if any(v.flag == "placeholder_name" for v in fired):
        out["mazal_id"] = ""
        out["viaf_id"] = ""
        out["wikidata_qid"] = ""
        payload.pop("mazal_id", None)
        payload.pop("viaf_id", None)
        payload.pop("wikidata_qid", None)
        for stale in ("gnd_id", "lc_id", "isni", "bnf_id"):
            payload.pop(stale, None)

    # Corporate routing: drop the person-style VIAF id when the guard
    # fired, mirroring desktop's _match_against_authorities short-circuit.
    if any(v.flag == "corporate_viaf_drop" for v in fired):
        out["viaf_id"] = ""
        payload.pop("viaf_uri", None)
        for stale in ("gnd_id", "lc_id", "isni", "bnf_id"):
            payload.pop(stale, None)

    out["confidence"] = confidence
    payload["guard_flags"] = new_flags
    if reasons:
        prior = payload.get("reasoning") or ""
        payload["reasoning"] = (prior + " " + " ".join(reasons)).strip()
    out["payload"] = payload
    return out


__all__ = [
    "GuardVerdict",
    "HardeningContext",
    "apply_hardening_guards",
    "guard_cluster_collapse",
    "guard_corporate_meeting",
    "guard_mazal_pair_collision",
    "guard_nli_strict_skip_viaf",
    "guard_placeholder_name",
    "guard_short_name_homonym",
    "guard_wikidata_crosscheck",
]
