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
        states.extend((s, False) for s in _claim_states(payload, answers))
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
