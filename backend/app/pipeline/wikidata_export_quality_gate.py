"""Hard export-quality gate for Wikidata Studio builds.

Findings come in two severities and the distinction is the whole design:

* **blocking** — a build bug. The projection asserted something the evidence does
  not support, or lost an identity it was given. A curator cannot override these
  (Rule W-137); they clear by fixing a lookup table or a projection.
* **informational** — a reviewed decision or a sparse catalog record. Blocking on
  these would make a perfectly valid but thin MARC record unbuildable.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any

from converter.wikidata.item_validator import validate_item

logger = logging.getLogger(__name__)

# A label ending in a library signature: "Jerusalem, NLI, F 12345".
_LABEL_SHELFMARK_RE = re.compile(r"([A-Z]{1,3}\.?\s?\d[\d\-/. ]*)$")


def _statement_values(item: Any, property_id: str) -> list[str]:
    out: list[str] = []
    for stmt in getattr(item, "statements", []) or []:
        pid = str(
            getattr(stmt, "property_id", None)
            or (stmt.get("property_id") if isinstance(stmt, dict) else "")
            or (stmt.get("property") if isinstance(stmt, dict) else "")
            or "",
        )
        if pid != property_id:
            continue
        value = (
            getattr(stmt, "value", None)
            if not isinstance(stmt, dict) else stmt.get("value")
        )
        text = str(value or "").strip()
        if text:
            out.append(text)
    return out


def _norm_shelfmark(text: str) -> str:
    return re.sub(r"[\s.]+", "", str(text or "")).upper()


def _manuscript_identity_errors(items: list[Any]) -> list[str]:
    """Catch cross-record contamination before it can reach public Wikidata.

    Three canonical manuscripts once shipped with the same label and the same
    P217 because the legacy MARC join matched on any linked control number
    (Rule W-137). Identity defects are build bugs, never curator decisions.
    """
    errors: list[str] = []
    by_identity: dict[tuple[str, str], list[str]] = defaultdict(list)
    for item in items:
        if str(getattr(item, "entity_type", "") or "").strip().lower() != "manuscript":
            continue
        local_id = str(getattr(item, "local_id", "") or "")
        label = _label_text(item)
        shelfmarks = _statement_values(item, "P217")
        catalog_ids = _statement_values(item, "P3959")

        if len(catalog_ids) > 1:
            errors.append(
                f"MANUSCRIPT_MULTIPLE_CATALOG_IDS {local_id}: "
                f"P3959 emitted {len(catalog_ids)}× ({', '.join(catalog_ids)}) — "
                "a manuscript item owns exactly one catalog record",
            )
        if len(shelfmarks) > 1:
            errors.append(
                f"MANUSCRIPT_MULTIPLE_SHELFMARKS {local_id}: "
                f"P217 emitted {len(shelfmarks)}× ({', '.join(shelfmarks)})",
            )

        match = _LABEL_SHELFMARK_RE.search(label)
        if match and shelfmarks:
            wanted = _norm_shelfmark(match.group(1))
            have = {_norm_shelfmark(s) for s in shelfmarks}
            if wanted and not any(wanted in s or s in wanted for s in have):
                errors.append(
                    f"LABEL_SHELFMARK_MISMATCH {local_id}: label '{label}' "
                    f"does not match P217 {shelfmarks}",
                )
        if label and shelfmarks:
            by_identity[(label.casefold(), _norm_shelfmark(shelfmarks[0]))].append(local_id)

    for (label, shelfmark), local_ids in by_identity.items():
        if len(local_ids) > 1:
            errors.append(
                f"MANUSCRIPT_SHARED_IDENTITY {', '.join(sorted(local_ids))}: "
                f"{len(local_ids)} manuscript items share label '{label}' and "
                f"shelfmark '{shelfmark}'",
            )
    return errors


def _label_text(item: Any) -> str:
    labels = getattr(item, "labels", {}) or {}
    if isinstance(labels, dict):
        return str(
            labels.get("en") or labels.get("he") or next(iter(labels.values()), "") or "",
        ).strip()
    return ""


def _work_title_errors(items: list[Any]) -> list[str]:
    """A work carries one title claim — its own (Rule W-138)."""
    errors: list[str] = []
    for item in items:
        if str(getattr(item, "entity_type", "") or "").strip().lower() != "work":
            continue
        titles = _statement_values(item, "P1476")
        if len(titles) > 1:
            errors.append(
                f"WORK_MULTIPLE_TITLES {getattr(item, 'local_id', '')}: "
                f"P1476 emitted {len(titles)}× ({', '.join(titles)})",
            )
    return errors


_NLI_LABEL_RE = re.compile(r"\bJerusalem,\s*NLI\b", re.IGNORECASE)
_QID_VALUE_RE = re.compile(r"Q\d+")


def _static_projected_qids() -> frozenset[str]:
    """QIDs the projection picks from a table WE maintain (Rule W-164).

    Everything else on a statement was reconciled at runtime, so no table exists
    to gloss it from.
    """
    from converter.wikidata import property_mapping  # noqa: PLC0415
    from converter.wikidata.holding_institutions import _INSTITUTIONS  # noqa: PLC0415

    out: set[str] = set(_INSTITUTIONS)
    for name in dir(property_mapping):
        value = getattr(property_mapping, name)
        if isinstance(value, str) and _QID_VALUE_RE.fullmatch(value):
            out.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                for candidate in (item if isinstance(item, (list, tuple)) else [item]):
                    if isinstance(candidate, str) and _QID_VALUE_RE.fullmatch(candidate):
                        out.add(candidate)
    return frozenset(out)


def _holder_findings(
    items: list[Any],
    marc_records: list[dict[str, Any]] | None,
) -> tuple[list[str], list[str]]:
    """Holder-resolution findings (Rule W-161).

    Blocking: a holder name nobody has audited, and an invented NLI label. Both
    are build bugs a lookup-table row fixes — never a curator decision (W-137).
    Informational: a reviewed abstention, and a record that attests no holder.
    """
    from app.pipeline.marc_verify_context import (  # noqa: PLC0415
        canonical_control_number,
        index_marc_records,
    )
    from converter.wikidata.holding_institutions import (  # noqa: PLC0415
        STATUS_ABSTAINED,
        STATUS_PLACEHOLDER,
        STATUS_UNKNOWN,
        resolve_holder,
    )
    from converter.wikidata.item_builder import (  # noqa: PLC0415
        holder_names_from_record,
    )

    blocking: list[str] = []
    informational: list[str] = []
    by_cn = index_marc_records(marc_records or []) if marc_records else {}

    for item in items:
        if str(getattr(item, "entity_type", "") or "").strip().lower() != "manuscript":
            continue
        local_id = str(getattr(item, "local_id", "") or "")
        label = _label_text(item)

        record: dict[str, Any] = {}
        for cn in _statement_values(item, "P3959") or list(
            getattr(item, "records", None) or [],
        ):
            found = by_cn.get(canonical_control_number(cn) or "")
            if found:
                record = dict(found)
                break

        names = holder_names_from_record(record) if record else []
        resolutions = [resolve_holder(name) for name in names]
        attested = [r for r in resolutions if r.attested]

        for resolution in attested:
            if resolution.status == STATUS_UNKNOWN:
                blocking.append(
                    f"UNAUDITED_HOLDER {local_id}: holder {resolution.name!r} is not "
                    "in the audited holding-institution table — verify the QID live "
                    "and add an entry, or record an abstention with the reason",
                )
            elif resolution.status == STATUS_ABSTAINED:
                informational.append(
                    f"HOLDER_ABSTAINED {local_id}: {resolution.name!r} — "
                    f"{resolution.reason}",
                )
        if record and not attested:
            informational.append(
                f"HOLDER_UNATTESTED {local_id}: the record names no current holder",
            )

        # The label may only say NLI when a holder actually resolved to NLI.
        if _NLI_LABEL_RE.search(label):
            nli_attested = any(r.qid == "Q188915" for r in attested)
            if not nli_attested:
                names_text = ", ".join(r.name for r in attested) or "none"
                blocking.append(
                    f"FABRICATED_HOLDER {local_id}: label {label!r} claims NLI but "
                    f"the record attests {names_text}",
                )

        # A P195 we emit must be the QID the table verified for that holder.
        for qid in _statement_values(item, "P195"):
            if attested and not any(r.qid == qid for r in attested):
                resolved = ", ".join(f"{r.name}={r.qid}" for r in attested)
                blocking.append(
                    f"HOLDER_QID_UNVERIFIED {local_id}: P195={qid} does not match "
                    f"the audited holder ({resolved})",
                )

        if (
            record
            and attested
            and all(r.status == STATUS_PLACEHOLDER for r in resolutions)
        ):  # pragma: no cover — defensive; `attested` excludes placeholders
            informational.append(f"HOLDER_UNATTESTED {local_id}")

    return blocking, informational


def _claim_provenance_findings(
    serialised_items: list[dict[str, Any]],
    marc_records: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Every projected claim must be traceable to a channel (Rule W-162).

    Blocking: a statement PID with no ``claim_sources`` row at all, and one whose
    row says ``no_channel_mapped`` — both mean the judge is shown a claim it cannot
    trace, which is how 21 rows (P3342 ×16, P1891 ×5) shipped as
    ``channels: ["unmapped"], supported: false``.

    Informational: ``channel_empty``. The channel exists and this record's field is
    simply empty. Blocking on that would make a sparse but perfectly valid
    catalogue record unbuildable.
    """
    from copy import deepcopy  # noqa: PLC0415

    from app.pipeline.wikidata_verify_evidence import (  # noqa: PLC0415
        SUPPORT_CHANNEL_EMPTY,
        SUPPORT_NO_CHANNEL,
        enrich_items_with_verify_evidence,
    )

    blocking: list[str] = []
    informational: list[str] = []
    # Pure and offline: `_wikidata_existence` and `_llm_proposals` are absent at
    # build time, so no verify-time fact is consulted here.
    probe = deepcopy(serialised_items)
    enrich_items_with_verify_evidence(probe, marc_records)

    for item in probe:
        local_id = str(item.get("local_id") or item.get("_local_id") or "")
        claim_sources = (item.get("verify_evidence") or {}).get("claim_sources") or {}
        emitted = {
            str(stmt.get("property") or stmt.get("property_id") or "")
            for stmt in item.get("statements") or []
            if isinstance(stmt, dict)
        } - {""}

        for pid in sorted(emitted - set(claim_sources)):
            blocking.append(
                f"CLAIM_WITHOUT_PROVENANCE_ROW {local_id}: {pid} is projected but "
                "has no claim_sources row",
            )
        for pid, row in sorted(claim_sources.items()):
            if not isinstance(row, dict):
                continue
            status = str(row.get("support_status") or "")
            if status == SUPPORT_NO_CHANNEL:
                blocking.append(
                    f"CLAIM_WITHOUT_CHANNEL_ROW {local_id}: {pid} has no channel "
                    "table row — add one, or list the PID as qualifier/reference "
                    "only in property_mapping",
                )
            elif status == SUPPORT_CHANNEL_EMPTY:
                informational.append(
                    f"claim_channel_empty {local_id}: {pid} has a channel "
                    f"({', '.join(row.get('channels') or [])}) but this record's "
                    "field is empty",
                )

        # Rule W-80: a QID we project must come with a gloss, or the curator and
        # the judge are shown a bare Q-number and cannot check it.
        value_labels = (item.get("verify_evidence") or {}).get("value_labels") or {}
        for stmt in item.get("statements") or []:
            if not isinstance(stmt, dict):
                continue
            value = str(stmt.get("value") or "")
            if not _QID_VALUE_RE.fullmatch(value):
                continue
            gloss = str(value_labels.get(value) or stmt.get("value_label") or "")
            if gloss and gloss != value:
                continue
            pid = str(stmt.get("property") or stmt.get("property_id") or "")
            if value in _static_projected_qids():
                # A QID WE chose from a static table and then failed to gloss. The
                # gloss is how a wrong constant becomes visible — the audit that
                # found 24 of them started from exactly this check (Rule W-164).
                blocking.append(
                    f"MISSING_VALUE_LABEL {local_id} {value}: {pid} projects a "
                    "static QID with no label — add it to QID_LABELS or to the "
                    "audited holding-institution table",
                )
            else:
                # Reconciled at runtime (a KIMA place, a matched person). There is
                # no static table to add it to; the fix is for the reconciler to
                # stamp `value_label`, so this is a coverage gap, not a build bug.
                informational.append(
                    f"value_label_missing_for_reconciled_qid {local_id} {value}: "
                    f"{pid} — the reconciler did not stamp a label",
                )
    return blocking, informational


_ATTESTED_IN_RE = re.compile(r"attested in NLI record\s+(\d+)")


def _work_identity_findings(items: list[Any]) -> list[str]:
    """A work names ONE record, on every surface it names one at all (W-165).

    `QDraft_Work_37` shipped a label from the HMO snapshot, a description citing
    record 990000592310205171 (whose MARC 245 is a different work), and evidence
    sourced from 990001253400205171 — three sources, three answers.
    """
    errors: list[str] = []
    for item in items:
        if str(getattr(item, "entity_type", "") or "").strip().lower() != "work":
            continue
        local_id = str(getattr(item, "local_id", "") or "")
        records = {str(r) for r in (getattr(item, "records", None) or []) if r}
        evidence = [
            row for row in (getattr(item, "work_candidate_evidence", None) or [])
            if isinstance(row, dict)
        ]
        evidence_records = {
            str(row.get("source_record_id") or "")
            for row in evidence
        } - {""}

        descriptions = getattr(item, "descriptions", {}) or {}
        cited = {
            match.group(1)
            for text in descriptions.values()
            for match in [_ATTESTED_IN_RE.search(str(text or ""))]
            if match
        }
        for cn in sorted(cited - records - evidence_records):
            errors.append(
                f"WORK_EVIDENCE_RECORD_MISMATCH {local_id}: the description is "
                f"attested in record {cn}, which is neither in the item's records "
                f"{sorted(records) or '[]'} nor in its evidence "
                f"{sorted(evidence_records) or '[]'}",
            )
        for cn in sorted(evidence_records - records) if records else []:
            errors.append(
                f"WORK_EVIDENCE_RECORD_MISMATCH {local_id}: evidence cites record "
                f"{cn}, which is not among the item's records {sorted(records)}",
            )
    return errors


def _person_heading_findings(items: list[Any]) -> list[str]:
    """Report a refused authority heading, and an unconfirmed identity (W-166).

    Informational for now: the MARC heading already won the label slot and the
    dates are already suppressed, so nothing wrong ships. Promote to blocking once
    a clean run confirms the comparator is not over-refusing.
    """
    findings: list[str] = []
    for item in items:
        if str(getattr(item, "entity_type", "") or "").strip().lower() != "person":
            continue
        local_id = str(getattr(item, "local_id", "") or "")
        mismatch = getattr(item, "heading_mismatch", None)
        if isinstance(mismatch, dict) and mismatch.get("reason"):
            findings.append(f"PERSON_HEADING_MISMATCH {local_id}: {mismatch['reason']}")
        flags: set[str] = set()
        for row in getattr(item, "authority_evidence", None) or []:
            if isinstance(row, dict):
                flags |= {str(f) for f in (row.get("guard_flags") or [])}
        if "wikidata_crosscheck_fail" in flags:
            findings.append(
                f"PERSON_IDENTITY_UNCONFIRMED {local_id}: the authority row failed "
                "the Wikidata crosscheck — dates suppressed, MARC heading kept",
            )
    return findings


def wikidata_export_quality_report(
    items: list[Any],
    *,
    serialised_items: list[dict[str, Any]] | None = None,
    marc_records: list[dict[str, Any]] | None = None,
) -> dict[str, list[str]]:
    """Audit built items and split findings into blocking / informational.

    ``serialised_items`` and ``marc_records`` unlock the evidence-level checks,
    which need the serialised statement/claim shape the verify pack is built
    from. Callers that only have native items still get every identity check.
    """
    from app.pipeline.wikidata_local_refs import dangling_local_references  # noqa: PLC0415

    blocking: list[str] = _manuscript_identity_errors(items)
    informational: list[str] = []

    blocking.extend(
        f"DANGLING_LOCAL_REFERENCE {ref}: two-pass upload placeholder names an "
        "item this build did not produce"
        for ref in dangling_local_references(items)
    )
    blocking.extend(_work_title_errors(items))
    blocking.extend(_work_identity_findings(items))
    informational.extend(_person_heading_findings(items))
    for item in items:
        local_id = str(getattr(item, "local_id", "") or "")
        if not _label_text(item):
            blocking.append(f"MISSING_LABEL {local_id}: item has no label")
        for issue in validate_item(item):
            if issue.severity != "error":
                continue
            blocking.append(f"{issue.code} {local_id}: {issue.message}")

    if marc_records is not None:
        holder_blocking, holder_informational = _holder_findings(items, marc_records)
        blocking.extend(holder_blocking)
        informational.extend(holder_informational)

    if serialised_items and marc_records is not None:
        claim_blocking, claim_informational = _claim_provenance_findings(
            serialised_items, marc_records,
        )
        blocking.extend(claim_blocking)
        informational.extend(claim_informational)

    return {"blocking": blocking, "informational": informational}


def assert_wikidata_export_quality(
    items: list[Any],
    *,
    serialised_items: list[dict[str, Any]] | None = None,
    marc_records: list[dict[str, Any]] | None = None,
) -> None:
    """Raise when built items have blocking findings that indicate a build bug.

    Uses the full ``validate_item`` ERROR set so a bad projection cannot be
    cached or handed to the curator as clean Studio output. Informational
    findings are logged and never block.
    """
    report = wikidata_export_quality_report(
        items, serialised_items=serialised_items, marc_records=marc_records,
    )
    for finding in report["informational"]:
        logger.warning("wikidata export quality [informational] %s", finding)

    errors = report["blocking"]
    if not errors:
        return
    sample = errors[:12]
    suffix = f" (+{len(errors) - len(sample)} more)" if len(errors) > len(sample) else ""
    raise ValueError(
        f"Wikidata export quality gate failed with {len(errors)} issue(s){suffix}:\n"
        + "\n".join(sample),
    )
