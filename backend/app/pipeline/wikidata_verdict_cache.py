"""Content-addressed cache keys for Wikidata Studio item AI verdicts."""

from __future__ import annotations

from typing import Any

from app.pipeline.ai_verdict_cache_common import (
    normalise_shacl_issues,
    sanitise_stored_verdict,
)
from app.pipeline.inference_cache import canonical_hash
from app.pipeline.marc_verify_context import (
    index_marc_records,
    marc_context_for_item,
)

# Bumped to w174_v1 with Rule W-174: catalogue P973 shelfmark gate, Hebrew
# bracket expansion, audited P195 value labels, work-title alias strip.
# Prior: w173_v1 (related-works P1574 ladder + Hebrew preferred P8189 strip).
WIKIDATA_VERDICT_SCHEMA = "w174_v1"
WIKIDATA_VERDICT_KEY_VERSION = "records_marc_v6"


FINGERPRINT_STATEMENT_LIMIT = 40
_FINGERPRINT_STATEMENT_KEYS = (
    "property", "property_id", "property_label",
    "value", "value_id", "value_type", "value_label", "unit", "rank",
)


def fingerprint_statements(
    item: dict[str, Any],
    *,
    limit: int = FINGERPRINT_STATEMENT_LIMIT,
) -> list[dict[str, Any]]:
    """Statement projection shared by fixtures, persist slims, and keys.

    The verify worker releases full Studio payloads before persisting verdicts
    (Rule W-132), so the fingerprint MUST only read fields that survive
    ``slim_item_for_verdict_persist`` — otherwise the stored ``cache_key`` can
    never be reproduced and every verdict reads as stale (Rule W-136).
    """
    statements = item.get("statements")
    if not isinstance(statements, list):
        return []
    rows: list[dict[str, Any]] = []
    for statement in statements[:limit]:
        if not isinstance(statement, dict):
            continue
        row = {
            key: statement.get(key)
            for key in _FINGERPRINT_STATEMENT_KEYS
            if statement.get(key) not in (None, "")
        }
        if row:
            rows.append(row)
    return rows


# Evidence channels that must NOT key a verdict.
#
# The fingerprint answers "is this the same item state the judge saw?", so it may
# only contain state the READ path can reproduce. A channel that exists solely
# because the verify worker did external I/O cannot be reproduced by the review
# table, so keying on it makes every verdict read as stale (Rule W-136):
#   `marc`           — hashed separately.
#   `llm_proposals`  — advisory; the rubric forbids it from moving any axis.
#   `duplicate_check` — a live Wikidata probe that only the verify path runs; its
#                      own 7-day cache TTL governs freshness, not this key.
_EVIDENCE_KEYS_OUTSIDE_FINGERPRINT = ("marc", "llm_proposals")
_EVIDENCE_SUBKEYS_OUTSIDE_FINGERPRINT = {"wikidata_existing": ("duplicate_check",)}

# The pre-W-140 shape, kept only so verdicts written with `llm_proposals` in the
# key can be recognised and rewritten forward instead of vanishing from the table.
_LEGACY_EVIDENCE_KEYS_OUTSIDE_FINGERPRINT = ("marc",)


def fingerprint_verify_evidence(
    item: dict[str, Any],
    *,
    drop: tuple[str, ...] = _EVIDENCE_KEYS_OUTSIDE_FINGERPRINT,
) -> dict[str, Any]:
    """``verify_evidence`` minus the channels that must not key a verdict."""
    pack = item.get("verify_evidence")
    if not isinstance(pack, dict):
        return {}
    slim = dict(pack)
    for key in drop:
        slim.pop(key, None)
    if drop == _EVIDENCE_KEYS_OUTSIDE_FINGERPRINT:
        for channel, subkeys in _EVIDENCE_SUBKEYS_OUTSIDE_FINGERPRINT.items():
            value = slim.get(channel)
            if isinstance(value, dict) and any(k in value for k in subkeys):
                trimmed = dict(value)
                for subkey in subkeys:
                    trimmed.pop(subkey, None)
                slim[channel] = trimmed
    return slim


# The judge's evidence projection is NOT the fingerprint projection (Rule W-156).
#
# The fingerprint answers "is this the same item state the judge saw?" and so may
# only contain state the read path can reproduce. What the judge *reads* is a
# different question, and collapsing the two blinded it: the fixture was built
# with `fingerprint_verify_evidence`, so `duplicate_check` and `llm_proposals`
# were stripped from every prompt while the rubric asked about both. They always
# rendered as `{}`, the rubric's "unknown ⇒ do not conclude the item is new"
# branch fired on all 343 items, and 28 of 29 `partial` verdicts hedged on a
# probe that had in fact answered.
#
# MARC still travels separately, in `marc_extracted.json`.
JUDGE_EVIDENCE_KEYS_DROPPED = ("marc",)


def judge_evidence_projection(item: dict[str, Any]) -> dict[str, Any]:
    """``verify_evidence`` as the JUDGE must see it — every channel the rubric names."""
    pack = item.get("verify_evidence")
    if not isinstance(pack, dict):
        return {}
    slim = dict(pack)
    for key in JUDGE_EVIDENCE_KEYS_DROPPED:
        slim.pop(key, None)
    return slim


def record_ids_for_wikidata_item(item: dict[str, Any]) -> list[str]:
    """Return explicit source records, or recover them from P3959 references."""
    from app.pipeline.marc_verify_context import canonical_control_number  # noqa: PLC0415

    record_ids: set[str] = set()
    for key in ("record_ids", "records", "control_numbers"):
        stored = item.get(key)
        if isinstance(stored, list):
            for value in stored:
                cn = canonical_control_number(value)
                if cn:
                    record_ids.add(cn)
    for key in ("control_number", "_control_number", "record_id"):
        cn = canonical_control_number(item.get(key))
        if cn:
            record_ids.add(cn)
    for statement in item.get("statements") or []:
        if not isinstance(statement, dict):
            continue
        for reference in statement.get("references") or []:
            if not isinstance(reference, dict):
                continue
            snaks = reference.get("snaks")
            rows = snaks if isinstance(snaks, list) else [reference]
            for snak in rows:
                if not isinstance(snak, dict):
                    continue
                prop = snak.get("property") or snak.get("property_id")
                value = canonical_control_number(snak.get("value"))
                if prop == "P3959" and value:
                    record_ids.add(value)
    return sorted(record_ids)


def _record_ids(item: dict[str, Any]) -> list[str]:
    return record_ids_for_wikidata_item(item)


def attach_local_reference_targets(
    items: list[dict[str, Any]],
    *,
    catalog: list[dict[str, Any]] | None = None,
) -> None:
    """Attach evidence for every ``__LOCAL`` statement target.

    ``catalog`` is the lookup set for resolving targets. Subset AI verify passes
    the full Studio cache here so manuscript ``P1574`` / ``P3342`` edges still
    resolve when only the non-passing rows are in ``items`` (Rule W-170).
    """
    lookup = catalog if catalog is not None else items
    by_local_id = {
        str(item.get("_local_id") or item.get("local_id") or ""): item
        for item in lookup
        if str(item.get("_local_id") or item.get("local_id") or "")
    }
    for item in items:
        targets: dict[str, dict[str, Any]] = {}
        existing = item.get("local_reference_targets")
        if isinstance(existing, dict):
            targets.update(
                {
                    str(key): value
                    for key, value in existing.items()
                    if isinstance(value, dict)
                }
            )
        for statement in item.get("statements") or []:
            if not isinstance(statement, dict):
                continue
            values: list[Any] = [statement.get("value"), statement.get("value_id")]
            values.extend(
                qualifier.get("value")
                for qualifier in statement.get("qualifiers") or []
                if isinstance(qualifier, dict)
            )
            for value in values:
                text = str(value or "")
                if not text.startswith("__LOCAL:"):
                    continue
                target_id = text.removeprefix("__LOCAL:")
                target = by_local_id.get(target_id)
                if target is None:
                    continue
                target_labels = target.get("labels")
                target_label = (
                    target_labels.get("en") or target_labels.get("he")
                    if isinstance(target_labels, dict) else ""
                )
                if statement.get("value") == text and target_label:
                    statement["value_label"] = target_label
                labels = target_labels
                target_descriptions = target.get("descriptions")
                target_aliases = target.get("aliases")
                targets[target_id] = {
                    "entity_type": target.get("entity_type"),
                    "semantic_type": target.get("semantic_type") or "",
                    "labels": labels if isinstance(labels, dict) else {},
                    "descriptions": target_descriptions if isinstance(target_descriptions, dict) else {},
                    "aliases": target_aliases if isinstance(target_aliases, dict) else {},
                    "records": target.get("records") or target.get("record_ids") or [],
                    "existing_qid": target.get("existing_qid"),
                    "authority_evidence": target.get("authority_evidence") or [],
                }
        if targets:
            item["local_reference_targets"] = targets
        else:
            item.pop("local_reference_targets", None)


def marc_context_for_wikidata_item(
    item: dict[str, Any],
    marc_records: list[dict[str, Any]],
) -> dict[str, str]:
    index = index_marc_records(marc_records)
    return marc_context_for_item(
        {"control_numbers": _record_ids(item), "record_ids": _record_ids(item)},
        index,
    )


def attach_wikidata_marc_context(
    items: list[dict[str, Any]],
    marc_records: list[dict[str, Any]],
) -> None:
    """Attach the same MARC slice used by Wikidata verdict cache keys.

    Also ensures ``verify_evidence`` is present (Rule W-124) when callers
    attach context after a path that skipped ``_fetch_wikidata_verify_items``.
    """
    from app.pipeline.wikidata_verify_evidence import (  # noqa: PLC0415
        enrich_items_with_verify_evidence,
    )

    enrich_items_with_verify_evidence(items, marc_records)


def wikidata_verdict_query_summary(
    item: dict[str, Any],
    judge_model: str = "gemini-3.5-flash",
    *,
    evaluator: str = "wikidata_item",
    marc_context: dict[str, str] | None = None,
    evidence_drop: tuple[str, ...] = _EVIDENCE_KEYS_OUTSIDE_FINGERPRINT,
) -> dict[str, Any]:
    marc_slice = marc_context
    if marc_slice is None:
        raw = item.get("_marc_context")
        marc_slice = raw if isinstance(raw, dict) else {}

    summary: dict[str, Any] = {
        "local_id": str(item.get("_local_id") or item.get("local_id") or ""),
        "entity_type": str(item.get("entity_type") or ""),
        "record_ids": _record_ids(item),
        "labels": item.get("labels") or {},
        "descriptions": item.get("descriptions") or {},
        "aliases": item.get("aliases") or {},
        "statements": fingerprint_statements(item),
        "existing_qid": item.get("existing_qid"),
        "validation_issues": normalise_shacl_issues(item.get("validation_issues") or []),
        "authority_evidence": item.get("authority_evidence") or [],
        "work_candidate_evidence": item.get("work_candidate_evidence") or {},
        "local_reference_targets": item.get("local_reference_targets") or {},
        "verify_evidence": fingerprint_verify_evidence(item, drop=evidence_drop),
        "hmo_wikibase_id": item.get("hmo_wikibase_id"),
        "source_uri": item.get("source_uri"),
        "marc_context": marc_slice,
        "judge_model": judge_model,
        "evaluator": evaluator,
        "wikidata_verdict_schema": WIKIDATA_VERDICT_SCHEMA,
    }
    if evaluator == "wikidata_autofix":
        live = item.get("wikidata_live")
        if isinstance(live, dict):
            summary["wikidata_live_fingerprint"] = {
                "conflict_count": live.get("conflict_count"),
                "row_count": len(live.get("rows") or []),
                "qid": live.get("qid"),
            }
    return summary


def wikidata_verdict_input_fingerprint(
    item: dict[str, Any],
    judge_model: str = "gemini-3.5-flash",
    *,
    evaluator: str = "wikidata_item",
    marc_context: dict[str, str] | None = None,
    evidence_drop: tuple[str, ...] = _EVIDENCE_KEYS_OUTSIDE_FINGERPRINT,
) -> str:
    return canonical_hash(
        wikidata_verdict_query_summary(
            item,
            judge_model,
            evaluator=evaluator,
            marc_context=marc_context,
            evidence_drop=evidence_drop,
        ),
    )


def wikidata_verdict_stable_input_fingerprint(
    item: dict[str, Any],
    judge_model: str = "gemini-3.5-flash",
    *,
    evaluator: str = "wikidata_item",
) -> str:
    """Fingerprint durable item state independently of verify-run evidence."""
    stable_item = {
        **item,
        "verify_evidence": {},
        "local_reference_targets": {},
    }
    statements = item.get("statements")
    if isinstance(statements, list):
        stable_item["statements"] = [
            {
                key: value
                for key, value in statement.items()
                if not (
                    key == "value_label"
                    and str(statement.get("value") or "").startswith("__LOCAL:")
                )
            }
            if isinstance(statement, dict)
            else statement
            for statement in statements
        ]
    return wikidata_verdict_input_fingerprint(
        stable_item,
        judge_model,
        evaluator=evaluator,
        marc_context={},
    )


def wikidata_verdict_judge_model(ai_verdict: dict[str, Any] | None) -> str:
    if not ai_verdict:
        return "gemini-3.5-flash"
    return str(ai_verdict.get("model") or "gemini-3.5-flash")


def sanitise_stale_wikidata_verdict(
    item: dict[str, Any],
    stored: dict[str, Any] | None,
    *,
    judge_model: str | None = None,
    evaluator: str = "wikidata_item",
    marc_context: dict[str, str] | None = None,
    stable_item: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(stored, dict) or not stored:
        return None
    model = judge_model or wikidata_verdict_judge_model(stored)
    eval_id = str(stored.get("evaluator") or evaluator)
    stable_source = stable_item or item
    if stored.get("stable_cache_key") == wikidata_verdict_stable_input_fingerprint(
        stable_source, model, evaluator=eval_id,
    ):
        return {
            **stored,
            "cache_key": wikidata_verdict_input_fingerprint(
                item, model, evaluator=eval_id, marc_context=marc_context,
            ),
        }
    expected = wikidata_verdict_input_fingerprint(
        item,
        model,
        evaluator=eval_id,
        marc_context=marc_context,
    )
    current = sanitise_stored_verdict(stored, expected_fingerprint=expected)
    if current is not None:
        return current

    # Verdicts written while `llm_proposals` still keyed the fingerprint are
    # valid for the same item state — recognise that shape and rewrite the key
    # forward rather than showing the curator an empty verdict column (W-140).
    legacy = sanitise_stored_verdict(
        stored,
        expected_fingerprint=wikidata_verdict_input_fingerprint(
            item,
            model,
            evaluator=eval_id,
            marc_context=marc_context,
            evidence_drop=_LEGACY_EVIDENCE_KEYS_OUTSIDE_FINGERPRINT,
        ),
    )
    if legacy is not None:
        return {**legacy, "cache_key": expected}

    # Derived evidence packs are scope-dependent (a subset verify run resolves
    # fewer ``__LOCAL`` targets), so a verdict keyed without them is still
    # valid for the same curator-visible item state.
    evidence_free = {**item, "verify_evidence": {}, "local_reference_targets": {}}
    without_evidence = sanitise_stored_verdict(
        stored,
        expected_fingerprint=wikidata_verdict_input_fingerprint(
            evidence_free,
            model,
            evaluator=eval_id,
            marc_context=marc_context,
        ),
    )
    if without_evidence is not None:
        return {**without_evidence, "cache_key": expected}

    if stored.get("cache_key_version"):
        return None

    legacy_item = {**item, "record_ids": _record_ids(item)}
    legacy = sanitise_stored_verdict(
        stored,
        expected_fingerprint=wikidata_verdict_input_fingerprint(
            legacy_item,
            model,
            evaluator=eval_id,
            marc_context={},
        ),
    )
    if legacy is None:
        return None
    return {
        **legacy,
        "cache_key": expected,
        "cache_key_version": WIKIDATA_VERDICT_KEY_VERSION,
    }


def cached_verdict_needs_duplicate_rejudge(
    cached: dict[str, Any] | None,
    item: dict[str, Any],
) -> bool:
    """True when the judge answered without a duplicate answer and now has one.

    Rule W-157. This deliberately does NOT touch the fingerprint. Keying a verdict
    on the raw probe payload is what Rule W-136 forbids — the review table cannot
    reproduce a live probe, so every verdict would read as stale. Instead the
    verdict records the coarse *class* it was judged under, and a transition from
    `unknown` to `probed-conclusive` sends that one item back to the judge.

    Terminating by construction: after the re-judge the stored class is conclusive,
    so the item is no longer eligible; if the probe is still inconclusive it was
    never eligible.
    """
    from app.pipeline.wikidata_duplicate_probe import (  # noqa: PLC0415
        DUP_CLASS_CONCLUSIVE,
        DUP_CLASS_UNKNOWN,
        duplicate_class_for_item,
    )

    if not isinstance(cached, dict):
        return False
    verdict = cached.get("verdict") if isinstance(cached.get("verdict"), dict) else cached
    stored_class = str(verdict.get("duplicate_class") or "")
    # A verdict written before Rule W-157 has no class at all. It was judged with
    # the probe stripped from its fixture, so `unknown` is the truthful reading.
    if not stored_class:
        stored_class = DUP_CLASS_UNKNOWN
    return (
        stored_class == DUP_CLASS_UNKNOWN
        and duplicate_class_for_item(item) == DUP_CLASS_CONCLUSIVE
    )


def _verdict_envelope(cached: dict[str, Any]) -> dict[str, Any]:
    """Normalise override-row vs inference-cache envelopes."""
    if isinstance(cached.get("verdict"), dict):
        return cached
    # Override rows store axes at the top level.
    overall = str(cached.get("overall") or "").strip().lower()
    if overall:
        return {
            "verdict": cached,
            "judge_id": cached.get("model") or cached.get("judge_id"),
            "judged_at": cached.get("judged_at"),
            "cache_key": cached.get("cache_key"),
            "evaluator": cached.get("evaluator") or cached.get("evaluator_id"),
            "confidence": cached.get("confidence"),
        }
    return cached


def cached_verdict_is_sticky_full(
    cached: dict[str, Any] | None,
    item: dict[str, Any],
    *,
    judge_model: str,
    evaluator_id: str,
) -> bool:
    """True when a prior *full* verdict may skip re-judge (Rule W-171).

    Sticky-full applies only when the strict claim fingerprint still matches.
    Schema-only bumps that leave claims unchanged keep the same fingerprint
    fields except ``wikidata_verdict_schema`` — those still miss the inference
    cache, but an override / prior envelope whose ``cache_key`` equals the
    *claims* fingerprint (schema-agnostic) may stick. Partial/fail never stick.
    """
    if not isinstance(cached, dict):
        return False
    envelope = _verdict_envelope(cached)
    verdict = envelope.get("verdict") if isinstance(envelope.get("verdict"), dict) else {}
    overall = str(verdict.get("overall") or "").strip().lower()
    if overall not in {"full", "pass"}:
        return False
    reasoning = str(verdict.get("reasoning") or "").strip()
    if not reasoning:
        return False
    if verdict.get("judge_failure") or envelope.get("judge_failure"):
        return False
    stored_model = str(
        envelope.get("judge_id") or verdict.get("model") or ""
    ).strip()
    if stored_model and stored_model != str(judge_model or "").strip():
        return False
    stored_eval = str(
        envelope.get("evaluator") or verdict.get("evaluator") or evaluator_id
    ).strip()
    if stored_eval and stored_eval != str(evaluator_id or "").strip():
        return False
    if cached_verdict_needs_duplicate_rejudge(envelope, item):
        return False

    stored_key = str(envelope.get("cache_key") or verdict.get("cache_key") or "").strip()
    if not stored_key:
        return False
    current = wikidata_verdict_input_fingerprint(
        item, judge_model, evaluator=evaluator_id,
    )
    if stored_key == current:
        return True
    # Schema-agnostic claims match: allow sticky across non-payload schema bumps.
    claims_now = wikidata_claims_fingerprint(item, judge_model, evaluator=evaluator_id)
    claims_stored = str(
        verdict.get("claims_fingerprint")
        or envelope.get("claims_fingerprint")
        or ""
    ).strip()
    if claims_stored and claims_stored == claims_now:
        return True
    return False


def wikidata_claims_fingerprint(
    item: dict[str, Any],
    judge_model: str = "gemini-3.5-flash",
    *,
    evaluator: str = "wikidata_item",
    marc_context: dict[str, str] | None = None,
) -> str:
    """Strict fingerprint with the schema salt removed (sticky-full helper)."""
    summary = wikidata_verdict_query_summary(
        item, judge_model, evaluator=evaluator, marc_context=marc_context,
    )
    summary = dict(summary)
    summary.pop("wikidata_verdict_schema", None)
    return canonical_hash(summary)


def annotate_duplicate_rejudge(
    verdict: dict[str, Any] | None,
    item: dict[str, Any],
) -> dict[str, Any] | None:
    """Flag a verdict whose duplicate answer has since resolved. Never drops one.

    A read path that discarded the verdict here would lose a real judgement over a
    channel the judge was not shown; the curator is told it is worth re-running
    instead.
    """
    if not isinstance(verdict, dict):
        return verdict
    if not cached_verdict_needs_duplicate_rejudge(verdict, item):
        return verdict
    return {
        **verdict,
        "needs_rejudge": True,
        "rejudge_reason": "duplicate_check_resolved",
    }
