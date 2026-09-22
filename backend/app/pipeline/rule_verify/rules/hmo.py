"""HMO Wikibase Studio rule catalog.

Two families:

* Wrapped validators — every existing deterministic check becomes its own
  rule so a curator can block approval per check: the SHACL issue codes
  and the ``hmo_export_quality`` audit codes.
* New deterministic checks — claim/datatype consistency, MARC grounding,
  within-run duplicates, and the API-backed live checks (Wikibase drift,
  Wikidata QID liveness, Wikidata label candidates) that reuse the
  throttled Action API helpers (Rule W-139).

Every rule is advisory. ``error`` (API down, evidence missing) never
reads as pass and never folds into ``fail`` (the abstain contract). A
protective budget cap or a rate-limited probe that never executed is
``not_relevant`` (W-255), not ``error`` — the budget is an operational
guard, not a data defect.
"""

from __future__ import annotations

import re
from typing import Any

from app.pipeline.rule_verify.base import (
    STATE_ERROR,
    Rule,
    RuleResult,
    fail,
    not_relevant,
    warn_as_pass,
)
from app.pipeline.rule_verify.context import RuleContext

_QID_RE = re.compile(r"^Q\d+$")
_PID_RE = re.compile(r"^P\d+$")
_HEBREW_RE = re.compile(r"[\u0590-\u05ea]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_QTY_VALUE_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")
_TIME_VALUE_RE = re.compile(r"^[+-]\d{1,4}-\d{2}-\d{2}T00:00:00Z$")


class SimpleRule(Rule):
    """A Rule built from a plain function (keeps the catalog declarative)."""

    def __init__(
        self, *, id: str, title: str, description: str, run_fn: Any,  # noqa: A002
        uses_api: bool = False,
    ) -> None:
        super().__init__(
            id=id, title=title, description=description,
            channel="hmo", uses_api=uses_api,
        )
        self._run_fn = run_fn

    def run(self, entity: dict[str, Any], ctx: Any) -> RuleResult:
        return self._run_fn(entity, ctx)


def _labels(entity: dict[str, Any]) -> dict[str, str]:
    labels = entity.get("labels")
    return {k: str(v) for k, v in labels.items() if v} if isinstance(labels, dict) else {}


def _descriptions(entity: dict[str, Any]) -> dict[str, str]:
    descs = entity.get("descriptions")
    return {k: str(v) for k, v in descs.items() if v} if isinstance(descs, dict) else {}


def _claims(entity: dict[str, Any]) -> list[dict[str, Any]]:
    claims = entity.get("claims")
    return [c for c in claims if isinstance(c, dict)] if isinstance(claims, list) else []


def _audit_issues(entity: dict[str, Any]) -> dict[str, list[str]]:
    """Run the export-quality audit once per entity; group messages by code."""
    cached = entity.get("_rule_export_quality")
    if isinstance(cached, dict):
        return cached
    from converter.wikibase.hmo_export_quality import audit_entity_draft
    from converter.wikibase.models import WikibaseEntityDraft

    draft = WikibaseEntityDraft(
        local_id=str(entity.get("local_id") or ""),
        labels=_labels(entity),
        descriptions=_descriptions(entity),
        entity_type=str(entity.get("entity_type") or ""),
        class_uri=str(entity.get("class_qid") or entity.get("class_uri") or ""),
        source_uri=str(entity.get("source_uri") or ""),
        control_numbers=[str(x) for x in entity.get("control_numbers") or []],
    )
    out: dict[str, list[str]] = {}
    for issue in audit_entity_draft(draft):
        out.setdefault(issue.code, []).append(issue.message)
    entity["_rule_export_quality"] = out
    return out


def _scope_for_code(code: str) -> str:
    label_codes = ("label", "title", "quote", "node")
    return "labels" if any(k in code for k in label_codes) else "descriptions"


# ── SHACL ────────────────────────────────────────────────────────────────

_BLOCKING_SEVERITIES = frozenset({"Violation", "Error"})


def _shacl_issues(entity: dict[str, Any]) -> list[dict[str, Any]]:
    issues = entity.get("shacl_issues")
    return [i for i in issues if isinstance(i, dict)] if isinstance(issues, list) else []


def _run_shacl_blocking(entity: dict[str, Any], ctx: Any) -> RuleResult:
    issues = _shacl_issues(entity)
    blocking = [i for i in issues if str(i.get("severity") or "") in _BLOCKING_SEVERITIES]
    if not blocking:
        return warn_as_pass("hmo.shacl.blocking", "no blocking SHACL issues")
    return fail(
        "hmo.shacl.blocking", "shacl", f"{len(blocking)} blocking SHACL issue(s)",
        {"issues": blocking[:5]},
    )


def _run_shacl_warning(entity: dict[str, Any], ctx: Any) -> RuleResult:
    issues = _shacl_issues(entity)
    warnings = [i for i in issues if str(i.get("severity") or "") not in _BLOCKING_SEVERITIES]
    if not warnings:
        return warn_as_pass("hmo.shacl.warning", "no SHACL warnings")
    return fail(
        "hmo.shacl.warning", "shacl", f"{len(warnings)} SHACL warning(s)",
        {"issues": warnings[:5]},
    )


# ── structural ───────────────────────────────────────────────────────────

def _run_source_uri(entity: dict[str, Any], ctx: Any) -> RuleResult:
    if str(entity.get("source_uri") or "").strip():
        return warn_as_pass("hmo.source_uri.present")
    return fail("hmo.source_uri.present", "source_uri", "item has no source URI")


def _run_class_qid(entity: dict[str, Any], ctx: Any) -> RuleResult:
    qid = str(entity.get("class_qid") or "").strip()
    if not qid:
        return fail("hmo.class.present", "class_qid", "item has no class QID")
    if not _QID_RE.match(qid):
        return fail("hmo.class.present", "class_qid", f"class_qid {qid!r} is not a QID")
    return warn_as_pass("hmo.class.present")


def _run_claims_present(entity: dict[str, Any], ctx: Any) -> RuleResult:
    claims = _claims(entity)
    if claims:
        return warn_as_pass("hmo.claims.present", f"{len(claims)} claim(s)")
    if str(entity.get("entity_type") or "") in {"E12_Production", "E52_Time-Span"}:
        return not_relevant("hmo.claims.present", "structural entity may be claim-free")
    return fail("hmo.claims.present", "claims", "item carries no claims")


_EXPECTED_DATATYPE_SHAPES: dict[str, Any] = {
    "wikibase-item": lambda v: bool(_QID_RE.match(str(v or ""))),
    "external-id": lambda v: bool(str(v or "").strip()) and " " not in str(v).strip(),
    "string": lambda v: bool(str(v or "").strip()),
    "monolingualtext": (
        lambda v: isinstance(v, dict) and bool(str(v.get("text") or "").strip())
    ),
    "url": lambda v: str(v or "").startswith(("http://", "https://")),
    "quantity": lambda v: bool(_qty_shape(v)),
    "time": lambda v: bool(
        _TIME_VALUE_RE.match(
            str(v.get("time") or "") if isinstance(v, dict) else str(v or "")
        )
    ),
    "globe-coordinate": (
        lambda v: isinstance(v, dict) and "latitude" in v and "longitude" in v
    ),
}


def _qty_shape(v: Any) -> bool:
    # Never `amount or ""` here: 0.0 is falsy, so a legitimate zero amount
    # (e.g. P232 part numbering) would read as an empty string and fail.
    amount = v.get("amount") if isinstance(v, dict) else v
    if amount is None:
        return False
    return bool(_QTY_VALUE_RE.match(str(amount)))


def _run_claim_datatypes(entity: dict[str, Any], ctx: Any) -> RuleResult:
    bad: list[dict[str, str]] = []
    for claim in _claims(entity):
        pid = str(claim.get("property_id") or "")
        datatype = str(claim.get("datatype") or "")
        value = claim.get("value")
        entry = None
        if pid and not _PID_RE.match(pid):
            entry = {
                "property_id": pid, "datatype": datatype, "value": str(value)[:80],
                "problem": "property id is not a PID",
            }
        else:
            shape = _EXPECTED_DATATYPE_SHAPES.get(datatype)
            if shape is not None and not shape(value):
                entry = {
                    "property_id": pid, "datatype": datatype, "value": str(value)[:80],
                    "problem": "value does not match declared datatype",
                }
        if entry is not None and entry not in bad:
            bad.append(entry)
    if not bad:
        return warn_as_pass("hmo.claims.datatype", "all claim values match their datatypes")
    return fail(
        "hmo.claims.datatype", "claims", f"{len(bad)} malformed claim value(s)",
        {"claims": bad[:5]},
    )


def _run_skipped_statements(entity: dict[str, Any], ctx: Any) -> RuleResult:
    skipped = entity.get("skipped_statements") or []
    if not skipped:
        return warn_as_pass("hmo.statements.resolved", "no unresolved statements")
    return fail(
        "hmo.statements.resolved", "skipped_statements",
        f"{len(skipped)} statement(s) could not be resolved to claims",
        {"skipped": [str(s)[:160] for s in skipped[:5]]},
    )


# ── MARC grounding ───────────────────────────────────────────────────────

def _run_marc_linked(entity: dict[str, Any], ctx: RuleContext) -> RuleResult:
    marc_index = ctx.marc_index or {}
    if not marc_index:
        return not_relevant("hmo.marc.linked", "no MARC records loaded for this run")
    control_numbers = [str(x) for x in entity.get("control_numbers") or [] if x]
    if not control_numbers:
        return fail("hmo.marc.linked", "control_numbers", "item carries no control numbers")
    found = [cn for cn in control_numbers if cn in marc_index]
    if found:
        return warn_as_pass("hmo.marc.linked", f"linked to {len(found)} run MARC record(s)")
    return fail(
        "hmo.marc.linked", "control_numbers",
        f"control numbers {control_numbers[:3]} not present in this run's MARC set",
        {"control_numbers": control_numbers[:10]},
    )


def _run_marc_label(entity: dict[str, Any], ctx: RuleContext) -> RuleResult:
    marc_index = ctx.marc_index or {}
    if not marc_index:
        return not_relevant("hmo.marc.label_grounded", "no MARC records loaded")
    cn = str(entity.get("_control_number") or "")
    if not cn:
        for value in entity.get("control_numbers") or []:
            cn = str(value)
            if cn in marc_index:
                break
    record = marc_index.get(cn)
    if record is None:
        return not_relevant("hmo.marc.label_grounded", "no MARC record for this item")
    haystack = " \u00b7 ".join(str(v) for v in record.values() if v)
    for label in _labels(entity).values():
        text = label.strip()
        if len(text) >= 4 and text in haystack:
            return warn_as_pass("hmo.marc.label_grounded", f"label found in MARC: {text[:60]}")
    return fail(
        "hmo.marc.label_grounded", "labels",
        "no item label appears in the linked MARC record",
        {"record_cn": cn},
    )


# ── within-run duplicates ────────────────────────────────────────────────

def _normalise_label(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def _own_record_cn(item: dict[str, Any]) -> str:
    """The MARC record this item itself belongs to, from its source URI.

    W-48 propagation gives shared hubs (a text tradition, a subject header,
    a corpus-wide work) the CN set of every linked manuscript, so the first
    ``control_numbers`` entry is a corpus-wide value, not a record identity.
    Only a CN present in the item's own source URI identifies the record the
    item was minted from (run 3494ebf5 re-measure: every 'תכלאל' expression
    across 96 manuscripts carried the same first CN and read as one group,
    and same-title/different-author works (``Work…_by_N``) share the
    corpus-wide first CN too).
    """
    source = str(item.get("source_uri") or "")
    if not source:
        return ""
    for value in item.get("control_numbers") or []:
        cn = str(value)
        if cn and cn in source:
            return cn
    return ""


def in_run_dup_index(items: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[str]]:
    """(class, normalised label, own control number) → local_ids.

    Control numbers enter the key because a shared generic title is normal
    across distinct manuscripts ("מגלת אסתר" names many scrolls) — only
    same-record twins (same class + label + MARC record) are duplicates.
    Only items with an own record CN (``_own_record_cn``) participate:
    corpus-shared nodes have no record identity to compare.
    """
    index: dict[tuple[str, str, str], list[str]] = {}
    for item in items:
        cls = str(item.get("class_qid") or "")
        lid = str(item.get("local_id") or "")
        if not lid:
            continue
        cn = _own_record_cn(item)
        if not cn:
            continue
        for label in _labels(item).values():
            index.setdefault((cls, _normalise_label(label), cn), []).append(lid)
    return {k: v for k, v in index.items() if len(v) > 1}


def _run_in_run_duplicate(entity: dict[str, Any], ctx: RuleContext) -> RuleResult:
    index = getattr(ctx, "in_run_dup_index", None) or {}
    if not index:
        return warn_as_pass("hmo.duplicate.in_run", "no within-run label twins in scope")
    cn = _own_record_cn(entity)
    if not cn:
        return not_relevant(
            "hmo.duplicate.in_run",
            "no own record identity — corpus-shared node cannot be a same-record twin",
        )
    labels = _labels(entity)
    cls = str(entity.get("class_qid") or "")
    seen = {str(entity.get("local_id") or "")}
    twins: list[dict[str, str]] = []
    for label in labels.values():
        for other_id in index.get((cls, _normalise_label(label), cn), []):
            if other_id in seen:
                continue
            seen.add(other_id)
            twins.append({"local_id": other_id, "matched_label": label[:80]})
    if not twins:
        return warn_as_pass("hmo.duplicate.in_run", "no same-record label twin in this run")
    return fail(
        "hmo.duplicate.in_run", "labels",
        f"{len(twins)} other item(s) share label, class AND MARC record",
        {"duplicates": twins[:5]},
    )


# ── language hygiene (rubric-derived) ────────────────────────────────────

def _run_description_language(entity: dict[str, Any], ctx: Any) -> RuleResult:
    # Structural/production descriptions legitimately quote the Hebrew
    # title and scribe ("Production of MS X ('סדור…', shelfmark F 22258)") —
    # the honest-negative carve-out from the AI rubric (Rule W-53). The
    # script rule applies to primary scholarly entities only.
    if str(entity.get("entity_type") or "") in {"E12_Production", "E52_Time-Span", "CU"}:
        return not_relevant(
            "hmo.description.language",
            "structural entity — production/time-span descriptions may quote Hebrew",
        )
    en = _descriptions(entity).get("en")
    if not en:
        return not_relevant("hmo.description.language", "no English description")
    hebrew = len(_HEBREW_RE.findall(en))
    latin = len(_LATIN_RE.findall(en))
    if hebrew and hebrew > latin:
        return fail(
            "hmo.description.language", "descriptions.en",
            f"English description embeds Hebrew-script text ({hebrew} Hebrew chars)",
            {"description": en[:160]},
        )
    return warn_as_pass("hmo.description.language")


def _run_label_language(entity: dict[str, Any], ctx: Any) -> RuleResult:
    he = _labels(entity).get("he")
    if not he:
        return not_relevant("hmo.label.language", "no Hebrew label")
    if _LATIN_RE.search(he) and not _HEBREW_RE.search(he):
        return fail(
            "hmo.label.language", "labels.he",
            f"Latin-only text in the Hebrew label slot: {he[:60]}",
        )
    return warn_as_pass("hmo.label.language")


# ── upload state ─────────────────────────────────────────────────────────

def _run_upload_outcome(entity: dict[str, Any], ctx: Any) -> RuleResult:
    if entity.get("status") != "created":
        return not_relevant("hmo.upload.outcome", "item is not on the wiki yet")
    if entity.get("upload_outcome") == "failed":
        return fail(
            "hmo.upload.outcome", "upload",
            str(entity.get("upload_message") or "last upload attempt failed"),
        )
    return warn_as_pass("hmo.upload.outcome")


# ── API-backed rules ─────────────────────────────────────────────────────

def _api_gate(rule_id: str, ctx: RuleContext) -> RuleResult | None:
    """Fail-closed gate: API rules need a fetcher; otherwise ``error``."""
    if not ctx.api_enabled or ctx.fetcher is None:
        return RuleResult(
            rule_id, STATE_ERROR, "",
            "external API evidence disabled for this run — the check did not execute",
        )
    return None


def _wikibase_entity(qid: str, ctx: RuleContext) -> dict[str, Any]:
    """One merged wbgetentities per item per pass — alive + label_drift
    share it via ``ctx.memo``.

    The two live rules previously fetched the same QID twice (``info``,
    then ``labels``); one ``info|labels`` request halves the per-item
    request count against the 1.1 s throttle domain (W-139)."""
    key = f"wb:{qid}"
    if key not in ctx.memo:
        payload = ctx.fetcher((
            f"{ctx.wikibase_endpoint.rstrip('/')}/w/api.php",
            {
                "action": "wbgetentities", "ids": qid,
                "props": "info|labels", "languages": "en|he",
                "format": "json", "formatversion": "2",
            },
        ))
        ctx.memo[key] = payload or {}
    return ctx.memo[key]


def _run_wikibase_alive(entity: dict[str, Any], ctx: RuleContext) -> RuleResult:
    qid = str(entity.get("wikibase_id") or "").strip()
    if not qid:
        return not_relevant("hmo.live.alive", "item is not on the wiki yet")
    blocked = _api_gate("hmo.live.alive", ctx)
    if blocked is not None:
        return blocked
    try:
        payload = _wikibase_entity(qid, ctx)
    except Exception as exc:  # noqa: BLE001 — network failure is never a fail
        return RuleResult("hmo.live.alive", STATE_ERROR, "", f"lookup failed: {exc}"[:200])
    ent = ((payload or {}).get("entities") or {}).get(qid) or {}
    if ent.get("missing") is not None or not ent:
        return fail(
            "hmo.live.alive", "wikibase_id", f"linked item {qid} is gone from the wiki",
        )
    return warn_as_pass("hmo.live.alive")


def _run_wikibase_label_drift(entity: dict[str, Any], ctx: RuleContext) -> RuleResult:
    qid = str(entity.get("wikibase_id") or "").strip()
    if not qid:
        return not_relevant("hmo.live.label_drift", "item is not on the wiki yet")
    blocked = _api_gate("hmo.live.label_drift", ctx)
    if blocked is not None:
        return blocked
    try:
        payload = _wikibase_entity(qid, ctx)
    except Exception as exc:  # noqa: BLE001
        return RuleResult(
            "hmo.live.label_drift", STATE_ERROR, "", f"lookup failed: {exc}"[:200],
        )
    live_labels = (((payload or {}).get("entities") or {}).get(qid) or {}).get("labels") or {}
    drift: list[dict[str, str]] = []
    for lang, built in _labels(entity).items():
        if lang not in ("en", "he"):
            continue
        cell = live_labels.get(lang) or {}
        live = str((cell.get("value") if isinstance(cell, dict) else cell) or "")
        if live and live != str(built):
            drift.append({"lang": lang, "built": str(built)[:80], "live": live[:80]})
    if not drift:
        return warn_as_pass("hmo.live.label_drift")
    return fail(
        "hmo.live.label_drift", "labels",
        f"live labels differ from the build on {len(drift)} language(s)",
        {"drift": drift[:4]},
    )


def _run_wikidata_qids_alive(entity: dict[str, Any], ctx: RuleContext) -> RuleResult:
    blocked = _api_gate("hmo.wikidata.qids_alive", ctx)
    if blocked is not None:
        return blocked
    qids = sorted({
        str(c.get("value") or "").strip()
        for c in _claims(entity)
        if str(c.get("datatype") or "") == "wikibase-item"
        and _QID_RE.match(str(c.get("value") or "").strip())
    })
    if not qids:
        return not_relevant("hmo.wikidata.qids_alive", "no Wikidata QID claims")
    budget_key = "wd_qid_budget"
    remaining = ctx.counters.get(budget_key)
    if remaining is None:
        import os  # noqa: PLC0415

        remaining = int(os.getenv("RULE_VERIFY_WD_QID_MAX", "2000"))
    if remaining <= 0:
        # W-255: budget exhaustion is a protective cap, not an execution
        # error — the check did not run, so it is not_relevant (fail-closed).
        return not_relevant(
            "hmo.wikidata.qids_alive",
            "QID liveness budget exhausted for this run — check did not execute",
        )
    wanted = qids[:remaining]
    ctx.counters[budget_key] = remaining - len(wanted)

    # The fetcher seam answers ("confirm_qids_alive", qids) with the same
    # mapping the real client returns; unknowns keep the abstain contract.
    results = ctx.fetcher("confirm_qids_alive", wanted)
    if not isinstance(results, dict):
        return RuleResult(
            "hmo.wikidata.qids_alive", STATE_ERROR, "", "liveness lookup unavailable",
        )
    dead = [qid for qid, alive in results.items() if alive is False]
    unknown = [qid for qid, alive in results.items() if alive is None]
    if dead:
        return fail(
            "hmo.wikidata.qids_alive", "claims",
            f"{len(dead)} dead Wikidata QID(s) referenced",
            {"dead": dead[:10], "unknown": unknown[:10]},
        )
    if unknown:
        return RuleResult(
            "hmo.wikidata.qids_alive", STATE_ERROR, "",
            f"liveness unknown for {len(unknown)} QID(s) — check did not complete",
            {"unknown": unknown[:10]},
        )
    return warn_as_pass("hmo.wikidata.qids_alive", f"{len(wanted)} QID claim(s) alive")


def _run_wikidata_label_candidates(entity: dict[str, Any], ctx: RuleContext) -> RuleResult:
    """CirrusSearch for live Wikidata items with this label — candidates only."""
    blocked = _api_gate("hmo.wikidata.label_candidates", ctx)
    if blocked is not None:
        return blocked
    labels = _labels(entity)
    title = (labels.get("en") or labels.get("he") or "").strip()
    if len(title) < 4:
        return not_relevant("hmo.wikidata.label_candidates", "label too short to probe")
    budget_key = "wd_probe_budget"
    if ctx.counters.get(budget_key, 1) <= 0:
        # W-255: a probe that never executed is not an execution error —
        # the budget is a protective cap (W-71), not a data defect. Not
        # folding into fail/pass keeps the rule fail-closed.
        return not_relevant(
            "hmo.wikidata.label_candidates",
            "probe budget exhausted for this run — check did not execute",
        )
    ctx.counters[budget_key] = ctx.counters.get(budget_key, 1) - 1
    try:
        candidates = ctx.fetcher("inlabel_search", title)
    except Exception as exc:  # noqa: BLE001 — rate-limit/network is never a fail
        return not_relevant(
            "hmo.wikidata.label_candidates",
            f"probe did not execute: {exc}"[:200],
        )
    if not candidates:
        return warn_as_pass("hmo.wikidata.label_candidates", "no live Wikidata label collision")
    return fail(
        "hmo.wikidata.label_candidates", "labels",
        f"{len(candidates)} live Wikidata item(s) carry this label — curator must confirm",
        {"candidates": list(candidates)[:8], "requires_curator_confirmation": True},
    )


# ── export-quality wrappers (one rule per issue code) ────────────────────

def _export_quality_rule(code: str) -> Rule:
    def _run(entity: dict[str, Any], ctx: Any, *, _code: str = code) -> RuleResult:
        rule_id = f"hmo.quality.{_code}"
        messages = _audit_issues(entity).get(_code)
        if not messages:
            return warn_as_pass(rule_id, "no issue")
        return fail(rule_id, _scope_for_code(_code), "; ".join(messages[:3]), {"codes": [_code]})

    return SimpleRule(
        id=f"hmo.quality.{code}",
        title=code.replace("_", " "),
        description=f"Export quality check wrapped from hmo_export_quality: {code}.",
        run_fn=_run,
    )


def _scope_for_code(code: str) -> str:
    label_markers = ("label", "title", "quote", "node")
    return "labels" if any(k in code for k in label_markers) else "descriptions"


_QUALITY_CODES: tuple[str, ...] = (
    "blank_node_exported",
    "hebrew_label_in_en_slot",
    "in_ms_label_noise",
    "generic_hmo_description",
    "work_missing_manuscript_scope",
    "missing_description",
    "missing_label",
    "production_missing_label",
    "production_description_repeats_label",
    "timespan_bare_label",
    "latin_label_in_he",
    "unbalanced_label_quotes",
    "witness_unusable_title",
)


def build_hmo_rules() -> list[Rule]:
    """The full HMO catalog, in stable display order."""
    rules: list[Rule] = [
        SimpleRule(
            id="hmo.shacl.blocking", title="blocking SHACL issues",
            description="Item has Violation/Error-level SHACL issues.",
            run_fn=_run_shacl_blocking,
        ),
        SimpleRule(
            id="hmo.shacl.warning", title="SHACL warnings",
            description="Item has Warning-level SHACL issues (advisory).",
            run_fn=_run_shacl_warning,
        ),
        SimpleRule(
            id="hmo.source_uri.present", title="source URI present",
            description="Every item must carry its ontology source URI.",
            run_fn=_run_source_uri,
        ),
        SimpleRule(
            id="hmo.class.present", title="class QID present",
            description="Item resolves to a schema class QID.",
            run_fn=_run_class_qid,
        ),
        SimpleRule(
            id="hmo.claims.present", title="claims present",
            description="Scholarly entities must carry at least one claim.",
            run_fn=_run_claims_present,
        ),
        SimpleRule(
            id="hmo.claims.datatype", title="claim datatypes well-formed",
            description="Every claim value matches its declared Wikibase datatype.",
            run_fn=_run_claim_datatypes,
        ),
        SimpleRule(
            id="hmo.statements.resolved", title="statements resolved",
            description="No statement was skipped during build resolution.",
            run_fn=_run_skipped_statements,
        ),
        SimpleRule(
            id="hmo.marc.linked", title="MARC record linked",
            description="Item control numbers exist in this run's MARC set.",
            run_fn=_run_marc_linked,
        ),
        SimpleRule(
            id="hmo.marc.label_grounded", title="label grounded in MARC",
            description="At least one item label appears in the linked MARC record.",
            run_fn=_run_marc_label,
        ),
        SimpleRule(
            id="hmo.duplicate.in_run", title="within-run duplicate",
            description="No other item in this run carries the same label and class.",
            run_fn=_run_in_run_duplicate,
        ),
        SimpleRule(
            id="hmo.description.language", title="English description script",
            description="English descriptions must not embed Hebrew-script text.",
            run_fn=_run_description_language,
        ),
        SimpleRule(
            id="hmo.label.language", title="Hebrew label script",
            description="Hebrew label slot must not carry Latin-only text.",
            run_fn=_run_label_language,
        ),
        SimpleRule(
            id="hmo.upload.outcome", title="last upload outcome",
            description="The most recent upload attempt of an on-wiki item succeeded.",
            run_fn=_run_upload_outcome,
        ),
        SimpleRule(
            id="hmo.live.alive", title="linked Wikibase item alive",
            description="A linked live Wikibase item still exists.",
            run_fn=_run_wikibase_alive, uses_api=True,
        ),
        SimpleRule(
            id="hmo.live.label_drift", title="live label matches build",
            description="Live en/he labels equal the build labels.",
            run_fn=_run_wikibase_label_drift, uses_api=True,
        ),
        SimpleRule(
            id="hmo.wikidata.qids_alive", title="Wikidata QID claims alive",
            description="Every referenced Wikidata QID resolves to a live item.",
            run_fn=_run_wikidata_qids_alive, uses_api=True,
        ),
        SimpleRule(
            id="hmo.wikidata.label_candidates", title="Wikidata label collisions",
            description=(
                "CirrusSearch probe for live Wikidata items with the same label — "
                "candidates need curator confirmation."
            ),
            run_fn=_run_wikidata_label_candidates, uses_api=True,
        ),
    ]
    for code in _QUALITY_CODES:
        rules.append(_export_quality_rule(code))
    return rules
