"""Deterministic Jev gates — ported verbatim from the bake-off harness.

``backend/scripts/typesafe_bakeoff.py`` is the source of truth: this module
ports ``_map_row`` (final-axes + overall computation), ``_deterministic_text_artifacts``
and ``ROLE_CONF_GATE`` to the production verdict shape. The gates are applied
by the session to a TypesafeJudge verdict, before ``parse_verdict`` — they
need the candidate payload, which the Judge protocol does not carry.

These gates were worth −10 unsafe approvals in the ablation; mechanical
artifact detection stays in code and is never put into model questions.
"""

from __future__ import annotations

from typing import Any

NAXIS, TAXIS, RAXIS = "name_ok", "type_ok", "role_ok"
AXES = (NAXIS, TAXIS, RAXIS)

# A blocking 'no' below this confidence is uncertainty, not a defect: gate it
# to 'partial' (curator review) instead of 'fail'. Calibrated against the
# 2026-09-20 bake-off (all wikidata role_ok=false-positives sat at conf<0.25).
ROLE_CONF_GATE = 0.5

# Per-claim statement checks: only wikidata_item, at most this many claims.
MAX_CLAIMS = 8

_AXIS_TO_STATE = {
    "yes": "pass", "partial": "partial", "no": "fail", "n/a": "not_applicable",
}
_MATCH_STATE = {
    "exact_or_vowelized": "pass",
    "trimmed_or_extended": "partial",
    "absent": "fail",
    "different_entity": "fail",
}
_NAME_QUALITY_STATE = {
    "specific_and_substantive": "pass",
    "system_label_ok": "pass",
    "generic_fallback": "partial",
    "malformed": "fail",
}


def norm_axis(value: Any) -> str:
    """Normalize one axis answer to the verdict vocabulary (harness `_norm_axis`)."""
    v = str(value or "").strip().lower()
    if v in ("not_applicable", "n/a", "na"):
        return "n/a"
    return v if v in ("yes", "partial", "no") else "unknown"


def universal_overall(axes: dict[str, str]) -> str:
    """The rubric's universal table: n/a→yes; any no→fail; else partial if any partial; else full.

    Never asked of the model — computed in code (Jev-primary rollout step 2).
    """
    vals = [str(axes.get(axis) or "unknown") for axis in AXES]
    if any(v == "no" for v in vals):
        return "fail"
    if any(v == "partial" for v in vals):
        return "partial"
    return "full"


def synthesize_reasoning(
    axes: dict[str, str],
    evidence_field: str,
    match_kind: str,
    answers: dict[str, Any],
) -> str:
    """Synthesized reasoning template (axes + evidence field + match kind +
    the wikidata type/duplicate checks + probabilities)."""
    parts = [
        f"{axis}={value}" for axis, value in axes.items() if value != "unknown"
    ]
    if match_kind and match_kind != "unknown":
        parts.append(f"match={match_kind}")
    if evidence_field and evidence_field != "none":
        parts.append(f"evidence in {evidence_field}")
    nq = answers.get("name_quality")
    if isinstance(nq, dict) and nq.get("choice"):
        parts.append(f"label quality: {nq['choice']}")
    p31 = (answers.get("p31_ok") or {}).get("choice") or ""
    if p31 and p31 != "yes":
        parts.append(f"P31 typing: {p31}")
    dup = (answers.get("duplicate_risk") or {}).get("choice") or ""
    if dup and dup != "no_duplicate_risk":
        parts.append(f"duplicate_risk: {dup}")
    tq = answers.get("text_quality")
    if isinstance(tq, dict) and isinstance(tq.get("noul"), (int, float)) \
            and tq["noul"] >= 0.5:
        parts.append(f"text artifacts flagged (noul={tq['noul']:.2f})")
    conf = answers.get(NAXIS, {}).get("confidence")
    if isinstance(conf, (int, float)):
        parts.append(f"p={conf:.2f}")
    return "; ".join(parts) + "."


_OPENER = {
    "full": "This item is safe to approve.",
    "partial": "This item needs curator attention before upload.",
    "fail": "Do not upload this item as-is.",
}

_AXIS_PASSED = {
    "name_ok": "The label is accurate.",
    "type_ok": "The entity type is correct.",
    "role_ok": "The statements check out.",
}

_AXIS_PROBLEM = {
    "name_ok": "The label needs attention.",
    "type_ok": "The entity type needs attention.",
    "role_ok": "The statements need attention.",
}


def _claim_evidence_lines(
    payload: dict[str, Any], answers: dict[str, Any], limit: int = 3,
) -> list[str]:
    """Concrete unsupported-claim names for the claims_supported rule."""
    out: list[str] = []
    statements = _statement_props(payload)
    for i, stmt in enumerate(statements[:MAX_CLAIMS]):
        ans = answers.get(f"claim_{i}")
        if not isinstance(ans, dict):
            continue
        noul = ans.get("noul")
        if not isinstance(noul, (int, float)) or noul >= 0.7:
            continue
        prop = str(stmt.get("property") or "?")
        label = str(stmt.get("property_label") or prop)
        value = str(stmt.get("value_label") or stmt.get("value") or "")
        out.append(f"{label} ({prop}) = {value}"[:90])
        if len(out) >= limit:
            break
    return out


def _plain_note(note: str) -> str:
    """Strip the leading 'axis:' token from a mechanical-gate note."""
    for axis in AXES:
        prefix = f"{axis}: "
        if note.startswith(prefix):
            note = note[len(prefix):]
            note = note[0].upper() + note[1:]
            break
    return note if note.endswith((".", "!", "?")) else note + "."


def explain_wikidata_verdict(
    axes: dict[str, str],
    overall: str,
    payload: dict[str, Any],
    answers: dict[str, Any],
    notes: list[str],
) -> str:
    """Human-readable explanation for a wikidata_item Jev verdict.

    Replaces the axis-token string ("role_ok=partial; evidence in title;
    p=0.85.") with curator sentences: an opener, the per-rule findings of
    the top-30 manifest that did not pass, the concrete unsupported claims,
    and the mechanical gate findings. Rule answers carry no free text, so
    the reasons come from the rule's own fix hint plus payload facts.
    """
    from eval_agent.wikidata_rules import rule_states

    sentences: list[str] = [_OPENER.get(overall, _OPENER["partial"])]
    passed: list[str] = []
    for axis in AXES:
        value = str(axes.get(axis) or "unknown")
        if value == "yes":
            passed.append(_AXIS_PASSED[axis])
    if passed and overall != "full":
        sentences.append(" ".join(passed))

    findings: list[str] = []
    for rule, state in rule_states(answers):
        if state == "pass":
            continue
        detail = rule.get("fix") or rule["statement"]
        if rule["id"] == "claims_supported":
            lines = _claim_evidence_lines(payload, answers)
            if lines:
                detail = "unsupported: " + "; ".join(lines) + ". " + detail
        findings.append(f"{'Problem' if state == 'fail' else 'Minor issue'}"
                        f" — {rule['name']}: {detail}")
    if not findings:
        # No per-rule answers (legacy cache or escalation): plain axis text.
        for axis in AXES:
            value = str(axes.get(axis) or "unknown")
            if value in ("partial", "no"):
                findings.append(_AXIS_PROBLEM[axis])
    sentences.extend(findings)

    for note in notes:
        sentences.append(_plain_note(note))

    dup = duplicate_check_from_payload(payload)
    dup_status = str(dup.get("status") or "")
    if dup_status == DUP_STATUS_HAS_QID:
        sentences.append(
            "This is an update to an existing Wikidata item — no duplicate "
            "risk.",
        )
    elif dup_status == "absent":
        sentences.append(
            "The duplicate check ran on live Wikidata: no existing item "
            "carries this identifier (absent).",
        )

    if overall == "full" and len(sentences) == 1:
        sentences.append(
            "Label, type, and statements all satisfy the entity-creation "
            "rules.",
        )
    p31 = (answers.get("p31_ok") or {}).get("choice") or ""
    if p31 == "no":
        sentences.append(
            "The instance-of (P31) class is wrong for this entity — wrong "
            "public modeling gets items deleted by the community.",
        )
    elif p31 == "partial":
        sentences.append(
            "The instance-of (P31) class is present but questionable as "
            "the primary class.",
        )
    dup = (answers.get("duplicate_risk") or {}).get("choice") or ""
    if overall != "fail" and dup == "unknown":
        sentences.append(
            "The duplicate check was inconclusive — it does not count "
            "against the item, but a curator may re-run it.",
        )
    return " ".join(sentences)


def deterministic_text_artifacts(
    payload: dict[str, Any], marc_context: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Mechanical label/description artifact checks (code, not judgment).

    Ported verbatim from the harness: unbalanced parentheses per text,
    item-CN vs description-CN mismatches, truncated folio ranges, and
    quote-collision garbles (spaces lost at a Hebrew gershayim boundary vs
    MARC 245).
    """
    import re as _re

    labels = payload.get("labels") or {}
    descs = payload.get("descriptions") or {}
    findings: list[tuple[str, str]] = []
    texts = [str(v) for v in list(labels.values()) + list(descs.values()) if v]
    for t in texts:
        if t.count("(") != t.count(")"):
            findings.append((
                "unclosed parenthesis",
                f"text has {t.count('(')} open vs {t.count(')')} close "
                f"parentheses: {t[:60]}",
            ))
            break
    cn = _re.compile(r"990\d{12,15}")
    # Identity CN = the one embedded in the item's own local id (W-137), not
    # the propagated linked set (W-48).
    identity_cns = set(
        cn.findall(str(payload.get("_local_id") or ""))
    ) or set(cn.findall(str(payload.get("source_uri") or "")))
    if not identity_cns:
        cns = [str(x) for x in payload.get("control_numbers") or []]
        identity_cns = {cns[0]} if cns else set()
    desc_cns = set(cn.findall(" ".join(str(v) for v in descs.values())))
    if identity_cns and desc_cns and not (desc_cns & identity_cns):
        findings.append((
            "manuscript mismatch",
            f"item identity CN {sorted(identity_cns)[0]} but description "
            f"cites {sorted(desc_cns)[0]}",
        ))
    if any(_re.search(r"folios? \d+[\s.]*$", t) for t in texts):
        findings.append((
            "truncated folio range",
            "a description ends on a folio number with no end value",
        ))
    if marc_context:
        title = str(marc_context.get("title") or "")

        def _loose(s: str) -> str:
            return _re.sub(r"[\s\"'״׳]+", "", s)

        if title:
            for v in (str(x) for x in list(labels.values()) + list(descs.values()) if x):
                # Garble = two abbreviations merged across a gershayim
                # boundary (e.g. שד"ר + כה"ר → שד"רכה"ר): a quote followed
                # by 2+ Hebrew letters. Legitimate abbreviations (שד"ר,
                # יצ"ו) keep exactly one letter after the quote.
                if len(v) >= 8 and _re.search(r"[א-ת]\"[א-ת]{2,}", v) \
                        and _loose(v) in _loose(title) and v not in title:
                    findings.append((
                        "quote-collision garble",
                        "label/description matches MARC 245 only with "
                        f"spaces/quotes collapsed: {v[:50]}",
                    ))
                    break
    return findings


def validator_error_present(payload: dict[str, Any]) -> bool:
    """Deterministic upload-gate check (wikidata_item): any ERROR-severity
    validation issue means the item is not publishable — a rule, not a
    judgment, so the confidence gate must never soften it."""
    for issue in payload.get("validation_issues") or []:
        if isinstance(issue, dict) and \
                str(issue.get("severity") or "").lower() == "error":
            return True
    return False


# Duplicate-check statuses (mirrors backend wikidata_duplicate_probe.py).
DUP_STATUS_CANDIDATES = "candidates_found"
DUP_STATUS_HAS_QID = "already_linked"

_WIKIDATA_ITEM = "wikidata_item"


def duplicate_check_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """The live duplicate probe result, from whichever surface carries it.

    Same precedence as backend ``duplicate_status_for_item`` (Rule W-159):
    ``_wikidata_existence.status`` → ``verify_evidence.wikidata_existing.
    duplicate_check`` → ``_duplicate_status``. ``not_run`` is a placeholder,
    never an answer.
    """
    surfaces: list[Any] = [
        (payload.get("_wikidata_existence") or {}).get("status")
        if isinstance(payload.get("_wikidata_existence"), dict) else None,
        (
            ((payload.get("verify_evidence") or {}).get("wikidata_existing")
             or {}).get("duplicate_check")
        ),
        payload.get("_duplicate_status"),
    ]
    for surface in surfaces:
        check = surface if isinstance(surface, dict) else {"status": surface}
        status = str(check.get("status") or "").strip()
        if status and status != "not_run":
            out = dict(check) if isinstance(check, dict) else {}
            out["status"] = status
            return out
    return {}


def duplicate_gate(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Deterministic duplicate gate (Rule W-139): an identifier match on live
    Wikidata is an identity match — creating another item is a duplicate,
    regardless of what the judge answered. Returns ``None`` when the gate is
    inconclusive (unknown statuses may not move any axis)."""
    check = duplicate_check_from_payload(payload)
    status = str(check.get("status") or "")
    if status != DUP_STATUS_CANDIDATES:
        return None
    if str(payload.get("existing_qid") or "").strip():
        return None  # UPDATE — no CREATE risk
    adoption = check.get("adoption") if isinstance(check, dict) else None
    if isinstance(adoption, dict) and adoption.get("adopted") is True:
        return None  # pipeline already adopted the matched QID
    candidates = [
        c for c in (check.get("candidates") or []) if isinstance(c, dict)
    ]
    qid = str((candidates[0] if candidates else {}).get("qid") or "")
    matched = str((candidates[0] if candidates else {}).get("matched_on") or "")
    return {"qid": qid, "matched_on": matched}


def has_p31_statement(payload: dict[str, Any]) -> bool:
    """True when the item carries at least one instance-of (P31) claim.

    P31 is structural (claim_sources marks it structural: true) — the build
    emits it on every item, so a missing P31 is a build defect, not catalog
    sparsity."""
    for stmt in payload.get("statements") or []:
        if isinstance(stmt, dict) and \
                str(stmt.get("property") or stmt.get("property_id") or "") == "P31":
            return True
    return False


def _statement_props(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in (payload.get("statements") or []) if isinstance(s, dict)]


def _prop_value(stmt: dict[str, Any]) -> str:
    return str(stmt.get("value") or stmt.get("value_label") or "")


def structural_findings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic top-30 rule checks beyond the three ported gates
    (artifact / validator-error / duplicate / P31-present): one title per
    work, one catalog record per manuscript, unresolved __LOCAL: targets,
    and comma-joined author-name strings. Each finding forces its axis in
    code — never a judgment, never softened by the confidence gate."""
    entity_type = str(payload.get("entity_type") or "")
    statements = _statement_props(payload)
    findings: list[dict[str, Any]] = []

    def _count(prop: str) -> int:
        return sum(
            1 for s in statements
            if str(s.get("property") or s.get("property_id") or "") == prop
        )

    if entity_type == "work" and _count("P1476") > 1:
        findings.append({
            "rule": "one title per work",
            "axis": "role_ok", "force": "no",
            "note": "the work carries more than one P1476 (title) claim — "
                    "a second title form is a defect",
        })
    if entity_type == "manuscript":
        if _count("P217") > 1:
            findings.append({
                "rule": "one catalog record per manuscript",
                "axis": "role_ok", "force": "no",
                "note": "the manuscript carries more than one P217 "
                        "(shelfmark) claim — a second shelfmark is "
                        "cross-record contamination",
            })
        if _count("P3959") > 1:
            findings.append({
                "rule": "one catalog record per manuscript",
                "axis": "role_ok", "force": "no",
                "note": "the manuscript carries more than one P3959 "
                        "(catalog ID) claim",
            })
    local_targets = payload.get("local_reference_targets")
    local_targets = local_targets if isinstance(local_targets, dict) else {}
    for s in statements:
        value = _prop_value(s)
        if value.startswith("__LOCAL:") and value not in local_targets:
            findings.append({
                "rule": "internal reference targets resolve",
                "axis": "role_ok", "force": "no",
                "note": f"{value} does not resolve in "
                        "local_reference_targets — build defect",
            })
            break
    for s in statements:
        prop = str(s.get("property") or s.get("property_id") or "")
        value = _prop_value(s)
        if prop == "P2093" and ", " in value:
            findings.append({
                "rule": "author-name strings hold one name",
                "axis": "role_ok", "force": "partial",
                "note": f"P2093 carries a comma-joined name list: {value[:60]}",
            })
            break
    return findings


def _answer_confidences(answers: dict[str, Any]) -> dict[str, Any]:
    return {
        q: a.get("confidence") if isinstance(a.get("confidence"), (int, float))
        else a.get("noul")
        for q, a in answers.items()
        if isinstance(a, dict) and isinstance(
            a.get("confidence", a.get("noul")), (int, float),
        )
    }


def _claim_states(payload: dict[str, Any], answers: dict[str, Any]) -> list[str]:
    """Per-claim check states (wikidata_item): pass/partial/fail, non-blocking."""
    states: list[str] = []
    claims = payload.get("statements") or []
    for i, stmt in enumerate(claims[:MAX_CLAIMS]):
        if not isinstance(stmt, dict):
            continue
        ans = answers.get(f"claim_{i}")
        if not isinstance(ans, dict):
            continue
        noul = ans.get("noul")
        if not isinstance(noul, (int, float)):
            continue
        if noul >= 0.7:
            states.append("pass")
        elif noul >= 0.4:
            states.append("partial")
        else:
            states.append("fail")
    return states


def _overall_from_states(states: list[tuple[str, bool]]) -> str:
    """Harness `_overall_from_checks` semantics: blocking fail → fail; any
    fail or partial → partial; else full. n/a and unknown contribute nothing
    (n/a→yes per the universal table)."""
    if any(state == "fail" and blocking for state, blocking in states):
        return "fail"
    if any(state in ("fail", "partial") for state, _ in states):
        return "partial"
    return "full"


def overall_from_answers(
    evaluator_id: str,
    final_axes: dict[str, str],
    answers: dict[str, Any],
    payload: dict[str, Any],
) -> str:
    """Full harness overall: axis checks (blocking) + the secondary check
    states (name_quality / text_quality for hmo, claims + p31_ok +
    duplicate_risk for wikidata, match_kind for NER)."""
    states: list[tuple[str, bool]] = [
        (_AXIS_TO_STATE.get(final_axes[axis], "error"), True) for axis in AXES
    ]
    name_quality = (answers.get("name_quality") or {}).get("choice") or ""
    tq = answers.get("text_quality") or {}
    artifact_noul = tq.get("noul") if isinstance(tq, dict) else None
    if evaluator_id == "hmo_wikibase_item":
        if name_quality:
            state = _NAME_QUALITY_STATE.get(name_quality, "error")
            states.append((state, state == "fail"))
        if isinstance(artifact_noul, (int, float)) and artifact_noul >= 0.5:
            states.append(("partial", False))
    if evaluator_id == _WIKIDATA_ITEM:
        # Claim-level noul in [0.4, 0.7) is SUPPORT UNCERTAINTY, not a defect:
        # the claim instructions define noul < 0.4 as "no channel names this
        # property" (unsupported → removable bad claim → caps at partial).
        # Capping on uncertainty made every bridge claim (P2888/P973/P217)
        # drag gold-full items to partial — so only an actually-unsupported
        # claim (fail) caps; partial contributes nothing.
        states.extend(
            ("fail", False)
            for s in _claim_states(payload, answers)
            if s == "fail"
        )
        p31 = (answers.get("p31_ok") or {}).get("choice") or ""
        if p31:
            # Wrong/missing instance-of typing is a real public-data problem
            # (the community deletes mistyped items) — blocking, like an axis.
            state = _AXIS_TO_STATE.get(p31, "error")
            states.append((state, state == "fail"))
        dup = (answers.get("duplicate_risk") or {}).get("choice") or ""
        if dup == "duplicate_found":
            # An identifier-matching item already exists: creating is a
            # duplicate (Rule W-139) — blocking.
            states.append(("fail", True))
        elif dup == "unknown":
            # An inconclusive check may not move any axis (Rule W-139).
            pass
        elif dup == "no_duplicate_risk":
            states.append(("pass", False))
    if evaluator_id not in ("genre_classifier", "hmo_wikibase_item",
                            _WIKIDATA_ITEM):
        match_kind = (answers.get("match_kind") or {}).get("choice") or ""
        if match_kind:
            states.append((_MATCH_STATE.get(match_kind, "error"), False))
    return _overall_from_states(states)


def apply_jev_gates(
    verdict: dict[str, Any],
    *,
    evaluator_id: str,
    candidate: Any,
    meta: dict[str, Any] | None,
) -> dict[str, Any]:
    """Finalize a TypesafeJudge verdict: rubric rule 5 (artifacts downgrade
    an accepted label) + the confidence gate (an unsure blocking 'no' routes
    to curator review, never to a hard fail) + the deterministic wikidata
    gates for ``wikidata_item`` (ERROR-severity validator issue; live
    duplicate probe says ``candidates_found`` without an adopted existing QID
    — Rule W-139; no P31 statement at all — structural claim missing), then
    recompute the overall (axes + name_quality/text_quality + p31_ok +
    duplicate_risk + claims/match_kind) and the synthesized reasoning.
    Mechanical gates are never softened by the confidence gate. Returns a new
    verdict dict — schema-shaped, no extra fields (verdict.v2
    additionalProperties: false).
    """
    answers = (meta or {}).get("answers")
    if not isinstance(answers, dict):
        return verdict
    payload = dict(getattr(candidate, "payload", None) or {})
    marc_context = getattr(candidate, "marc_context", None)

    raw_axes = {
        NAXIS: norm_axis((answers.get(NAXIS) or {}).get("choice")),
        TAXIS: norm_axis((answers.get(TAXIS) or {}).get("choice")),
        RAXIS: (
            norm_axis((answers.get(RAXIS) or {}).get("choice"))
            if RAXIS in answers else "n/a"
        ),
    }
    evidence = (answers.get("evidence_field") or {}).get("choice") or "unknown"
    match_kind = (answers.get("match_kind") or {}).get("choice") or ""
    confidences = _answer_confidences(answers)
    tq = answers.get("text_quality") or {}
    artifact_noul = tq.get("noul") if isinstance(tq, dict) else None
    artifact = isinstance(artifact_noul, (int, float)) and artifact_noul >= 0.5

    final = dict(raw_axes)
    if artifact and final[NAXIS] == "yes":
        final[NAXIS] = "partial"
    notes: list[str] = []
    # Axes forced by mechanical rules: the confidence gate must never soften
    # them (a rule answered by code, not a judgment answered by the model).
    forced: set[str] = set()
    validator_error = (
        validator_error_present(payload)
        if evaluator_id == _WIKIDATA_ITEM else False
    )
    if validator_error and final[RAXIS] != "no":
        final[RAXIS] = "no"
        forced.add(RAXIS)
        notes.append("role_ok: ERROR-severity validation issue — upload gate")
    if evaluator_id == _WIKIDATA_ITEM:
        dup = duplicate_gate(payload)
        if dup:
            final[TAXIS] = "no"
            forced.add(TAXIS)
            matched = dup["matched_on"] or "identifier match"
            qid_note = f" ({dup['qid']})" if dup["qid"] else ""
            notes.append(
                "type_ok: duplicate_check.status=candidates_found — "
                f"an item with this {matched} already exists{qid_note}; "
                "link instead of create (Rule W-139)",
            )
        if not has_p31_statement(payload):
            final[TAXIS] = "no"
            forced.add(TAXIS)
            notes.append(
                "type_ok: no P31 (instance of) statement on the item — "
                "structural claim missing; the build must emit it",
            )
    artifacts_code = (
        deterministic_text_artifacts(payload, marc_context)
        if evaluator_id == "hmo_wikibase_item" else []
    )
    if artifacts_code and final[NAXIS] == "yes":
        final[NAXIS] = "partial"
        notes.append(
            "name_ok: deterministic text artifacts ("
            + "; ".join(name for name, _ in artifacts_code) + ")",
        )
    if evaluator_id == _WIKIDATA_ITEM:
        # Top-30 manifest, deterministic half: structural findings force
        # their axis in code (never softened by the confidence gate).
        _FORCE_RANK = {"yes": 0, "partial": 1, "no": 2}
        for finding in structural_findings(payload):
            axis = finding["axis"]
            if str(final.get(axis)) == "n/a":
                continue
            if _FORCE_RANK[finding["force"]] > _FORCE_RANK.get(str(final.get(axis)), 0):
                final[axis] = finding["force"]
                forced.add(axis)
                notes.append(f"{axis}: {finding['note']}")
        # Judgment half: each per-rule answer reconciles into its axis in
        # code — overall stays code-computed (block rule R44).
        from eval_agent.wikidata_rules import (  # noqa: PLC0415
            rule_question_id,
            rule_states,
        )
        for rule, state in rule_states(answers):
            axis = rule["axis"]
            if axis in forced or str(final.get(axis)) == "n/a":
                continue
            conf = confidences.get(rule_question_id(rule["id"]))
            if state == "fail":
                if isinstance(conf, (int, float)) and conf < ROLE_CONF_GATE:
                    if final[axis] == "yes":
                        final[axis] = "partial"
                else:
                    final[axis] = "no"
                    forced.add(axis)
            elif state == "partial" and final[axis] == "yes":
                final[axis] = "partial"
    for axis in AXES:
        if axis in forced:
            continue
        conf = confidences.get(axis)
        if final[axis] == "no" and isinstance(conf, (int, float)) \
                and conf < ROLE_CONF_GATE:
            final[axis] = "partial"
            notes.append(f"{axis}: 'no' at confidence {conf:.2f} — routed to review")

    overall = overall_from_answers(
        evaluator_id, final, answers, payload,
    )
    if evaluator_id == _WIKIDATA_ITEM:
        reasoning = explain_wikidata_verdict(
            final, overall, payload, answers, notes,
        )
    else:
        reasoning = synthesize_reasoning(final, evidence, match_kind, answers)
        if notes:
            reasoning += " Confidence gate: " + "; ".join(notes) + "."
    gated = {
        "name_ok": final[NAXIS],
        "type_ok": final[TAXIS],
        "role_ok": final[RAXIS],
        "overall": overall,
        "reasoning": reasoning,
        "suggested_fix": verdict.get("suggested_fix"),
    }
    if verdict.get("publication_decision") is not None:
        gated["publication_decision"] = verdict["publication_decision"]
    return gated
