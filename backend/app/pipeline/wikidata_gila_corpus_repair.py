"""Prepare a complete, creator-checked repair plan before the first write."""

import asyncio
from collections import defaultdict
from copy import deepcopy

from app.pipeline import marc_occurrences as marc
from app.pipeline.wikidata_gila_audit import current_holding_label
from app.pipeline.wikidata_gila_execution import repair_claims, semantic_entity
from app.pipeline.wikidata_gila_people_batch import PlannedEdit, _load_items, repair_claim_builder
from app.pipeline.wikidata_gila_repairs import build_gila_repair_proposals, validate_gila_repair_proposal
from app.pipeline.wikidata_gila_sources import matching_claims, source_colophon
from app.pipeline.wikidata_reference_reader import ReferenceItemReader
from app.pipeline.wikidata_source_choices import _claim_values, digest
from app.publication.reviewed_reference_scope import GILA_CREATOR_UID
from converter.wikidata.item_models import WikidataStatement
from converter.wikidata.property_mapping import Q_KTIV, nli_catalog_url


def corpus_inventory(cases, points=frozenset({5, 8})):
    return {
        case["case_id"]: {
            "case_id": case["case_id"],
            "points": [point for point in case["points"] if point in points],
            "qid": case["qid"] if "qid" in case else None,
            "control_number": case.get("control_number"),
            "source_field": case.get("source_field"),
            "reason": case.get("completion_reason") or case.get("reason") or "source_evidence_required",
            "status": "other_creator" if case.get("completion_reason") == "item_has_other_creator" else "needs_evidence",
        }
        for case in cases if points.intersection(case["points"])
        and not case["case_id"].startswith("passage:")
    }


def source_corpus_inventory(cases, previous_cases=(), already_verified=frozenset(), *, reconciliations=()):
    current = {case["case_id"]: case for case in cases}
    combined = {case["case_id"]: case for case in previous_cases}
    combined.update(current)
    inventory = corpus_inventory(combined.values(), frozenset({8}))
    for case_id, row in inventory.items():
        case = combined[case_id]
        row["occurrence"] = case.get("occurrence") or case.get("source_field")
        row["source_value"] = case.get("source_value")
        if case_id not in current:
            row["source_snapshot"] = "baseline"
            if case_id in already_verified:
                row.update(status="already_verified", reason="verified_before_write")
            else:
                row.update(status="needs_review", reason="case_changed_since_last_audit")
        elif row["reason"] == "catalogue_status_evidence":
            row["status"] = "source_metadata"
    for citation in reconciliations:
        cn, occurrence = citation["control_number"], citation["occurrence"]
        case_id = f"source:{cn}:evidence:{occurrence}"
        original = citation["original_occurrence"]
        inventory[case_id] = {
            **citation, "case_id": case_id, "points": [8], "source_field": occurrence,
            "reason": "legacy_citation_reconciled", "status": "source_metadata",
            "original_case_id": f"source:{cn}:evidence:{original}",
        }
    return inventory


def contents_corpus_inventory(cases, previous_cases=(), already_verified=frozenset()):
    current = {row["case_id"]: row for row in cases}
    combined = {row["case_id"]: row for row in previous_cases}
    combined.update(current)
    inventory = corpus_inventory(combined.values(), frozenset({6}))
    for case_id, row in inventory.items():
        case = combined[case_id]
        row["occurrence"] = case.get("source_key") or case.get("occurrence")
        row["source_value"] = case.get("source_value")
        if case_id not in current:
            row["source_snapshot"] = "baseline"
            if case_id in already_verified:
                row.update(status="already_verified", reason="verified_before_write")
            else:
                row.update(status="needs_review", reason="case_changed_since_last_audit")
        elif row["status"] == "needs_evidence" and (
            case.get("live_status") in {"identity_review", "source_evidence"}
            or case.get("reason") == "stale_catalogue_edit"
        ):
            row["status"] = "needs_review"
    return inventory


def source_preflight_archive(rows, records, previous_records):
    def index_fields(snapshot, control_numbers):
        return {
            cn: {marc.source_key(field): field for field in marc.occurrences(snapshot[cn])}
            for cn in control_numbers if cn in snapshot
        }

    baseline_ids = {row["control_number"] for row in rows if row.get("source_snapshot") == "baseline"}
    current_ids = {row["control_number"] for row in rows if row.get("source_snapshot") != "baseline"}
    previous = index_fields(previous_records, baseline_ids)
    current = index_fields(records, current_ids)
    result = []
    for row in rows:
        cn = row["control_number"]
        occurrence = row.get("original_occurrence") or row.get("occurrence") or ""
        snapshot = previous if row.get("source_snapshot") == "baseline" else current
        fields = snapshot.get(cn, {})
        result.append({
            **row,
            "marc": fields.get(occurrence) or fields.get(occurrence.rsplit(":", 1)[0]),
            "source_url": nli_catalog_url(cn) if cn else None,
        })
    return result


def _is_current(entities, qid, rev):
    entity = entities.get(qid, {})
    return entity.get("id") == qid and entity.get("lastrevid") == rev and isinstance(rev, int)


def _source_patch(case, target, build_claim):
    proof = case["source_repair"]
    pid = proof["property"]
    cn = case["control_number"]
    reference = [
        {"property": "P248", "value": Q_KTIV, "type": "item", "value_namespace": "wikidata"},
        {"property": "P3959", "value": cn, "type": "external-id"},
        {"property": "P854", "value": nli_catalog_url(cn), "type": "url"},
        *({"property": "P854", "value": url, "type": "url"} for url in proof.get("proof_urls", [])
          if url != nli_catalog_url(cn)),
    ]
    qualifiers = [
        {"property": prop, "value": value, "type": "item" if prop == "P3831" else "string"}
        for prop, value in proof["qualifiers"].items()
    ]
    patch = build_claim(WikidataStatement(
        pid, proof["value"], proof["type"], qualifiers=qualifiers,
        references=reference, reference_blocks=[reference], language=proof["language"] or "he",
    ))
    if proof_text := proof.get("proof_text"):
        evidence, = patch["references"]
        evidence["snaks"]["P1683"] = [{
            "snaktype": "value", "property": "P1683", "datatype": "monolingualtext",
            "datavalue": {"type": "monolingualtext", "value": {"text": proof_text, "language": "en"}},
        }]
        evidence["snaks-order"].append("P1683")
    if claim_id := proof.get("extend_folio_claim"):
        original = deepcopy(next(row for row in target["claims"][pid] if row["id"] == claim_id))
        qualifiers = original.setdefault("qualifiers", {})
        original.setdefault("qualifiers-order", list(qualifiers)).append("P958")
        qualifiers["P958"] = patch["qualifiers"]["P958"]
        original.setdefault("references", []).extend(patch["references"])
        return original
    matches = matching_claims(target, pid, proof["value"], proof["qualifiers"], language=proof["language"])
    if not matches:
        return patch
    claim_id = matches[0]["claim_id"]
    original = deepcopy(next(row for row in target["claims"][pid] if row["id"] == claim_id))
    original.setdefault("references", []).extend(patch["references"])
    return original


def source_patches(case, target, build_claim):
    proof = case["source_repair"]
    return [
        _source_patch({**case, "source_repair": value}, target, build_claim)
        for value in (proof, *proof.get("additional_claims", ()))
    ]


def _exact_colophon(target, records):
    codes = set(_claim_values(target, "P3959"))
    if len(codes) == 1:
        return source_colophon(records.get(next(iter(codes))))
    return ""


def source_additions_only(plan, *, allow_folio=False):
    if plan.en_label is not None or plan.en_description is not None:
        return False
    existing = {
        claim["id"]: claim for values in plan.target["claims"].values() for claim in values if claim.get("id")
    }
    for patch in plan.patches:
        if "remove" in patch:
            return False
        if claim_id := patch.get("id"):
            before = existing.get(claim_id)
            if allow_folio and before and before.get("mainsnak", {}).get("property") == "P1574":
                qualifiers = before.get("qualifiers", {})
                added = patch.get("qualifiers", {}).get("P958", [])
                if "P958" not in qualifiers and len(added) == 1:
                    before = {**before, "qualifiers": {**qualifiers, "P958": added}}
                    before["qualifiers-order"] = [
                        *before.get("qualifiers-order", list(qualifiers)), "P958",
                    ]
            if not before or (
                {key: value for key, value in patch.items() if key != "references"}
                != {key: value for key, value in before.items() if key != "references"}
            ):
                return False
            references = before.get("references", [])
            if patch.get("references", [])[:len(references)] != references:
                return False
    return True


async def plan_sources(cases, records, *, check, progress, inventory, additions_only=False, reviewed_corrections=False):
    proposals = [
        case for case in cases
        if case.get("source_repair") or (not additions_only and case["case_id"].startswith("historical:"))
        if bool(case.get("source_repair", {}).get("retire_claim")) == reviewed_corrections
    ]
    if not proposals:
        return []
    await progress("sources_plan", 0, len(proposals), "cases", "Check item revisions for source proposals.")
    entities = {}
    revisions = {
        qid for c in proposals for qid in (
            c.get("source_repair", {}).get("qid") or c["qid"],
            *c.get("source_repair", {}).get("dependencies", {}),
        )
    }
    await _load_items(ReferenceItemReader(), revisions, entities, check)
    plans = []
    build_claim = repair_claim_builder()
    for case in proposals:
        case_id = case["case_id"]
        proof = case.get("source_repair", {})
        qid = proof.get("qid") or case["qid"]
        rev = proof.get("revision") or case.get("live_revision")
        dependencies = {**proof.get("dependencies", {}), qid: rev}
        if not all(_is_current(entities, key, value) for key, value in dependencies.items()):
            inventory[case_id].update(status="needs_review", reason="source_or_target_revision_changed", qid=qid)
            continue
        target = entities[qid]
        label = description = None
        if proof:
            patches = source_patches(case, target, build_claim)
            if reviewed_corrections:
                previous = next((claim for claim in target["claims"].get(proof["property"], [])
                                 if claim.get("id") == proof["retire_claim"]), None)
                if (
                    proof["property"] not in {"P217", "P1574"} or not previous
                    or previous.get("rank") == "deprecated"
                    or digest(semantic_entity(previous)) != proof["retire_digest"]
                    or len(patches) != 1 or patches[0].get("id") or "remove" in patches[0]
                ):
                    inventory[case_id].update(status="needs_review", reason="exact_source_proposal_unavailable")
                    continue
                patches.append({**previous, "rank": "deprecated"})
                label = proof.get("en_label")
                description = proof.get("en_description")
        else:
            proposals_at_revision = build_gila_repair_proposals(
                {qid: target}, colophons_by_qid={qid: _exact_colophon(target, records)},
            )
            if not proposals_at_revision:
                inventory[case_id].update(status="needs_review", reason="exact_source_proposal_unavailable")
                continue
            proposal = proposals_at_revision[0]
            validate_gila_repair_proposal(proposal, target)
            patches = repair_claims(proposal)
        plans.append(PlannedEdit(target, patches, [case_id], dependencies, en_label=label, en_description=description))
    return plans


async def plan_holding_labels(cases, records, *, check, inventory):
    candidates = [case for case in cases if case.get("holding_label") and case.get("qid")]
    if not candidates:
        return []
    items = {}
    await _load_items(ReferenceItemReader(), {case["qid"] for case in candidates}, items, check)
    plans = []
    for case in candidates:
        qid = case["qid"]
        revision = case.get("live_revision")
        target = items.get(qid)
        cn = case["control_number"]
        if not _is_current(items, qid, revision) or (
            current_holding_label(cn, records.get(cn), target) != case["holding_label"]
        ):
            inventory[case["case_id"]].update(status="needs_review", reason="source_or_target_revision_changed")
            continue
        plans.append(PlannedEdit(target, [], [case["case_id"]], {qid: revision}, en_label=case["holding_label"]))
    return plans


def _patch_key(patch):
    if patch.get("id"):
        return patch["id"]
    return tuple(repr(semantic_entity(patch.get(key))) for key in ("mainsnak", "qualifiers", "rank"))


def _merge_group(group):
    patches = {}
    revisions = {}
    labels = {plan.en_label for plan in group if plan.en_label is not None}
    descriptions = {plan.en_description for plan in group if plan.en_description is not None}
    if len(labels) > 1 or len(descriptions) > 1:
        return None
    for plan in group:
        if any(revisions.get(key, value) != value for key, value in plan.revisions.items()):
            return None
        revisions.update(plan.revisions)
        for patch in plan.patches:
            key = _patch_key(patch)
            if key not in patches:
                patches[key] = patch
                continue
            previous = patches[key]
            if semantic_entity({k: v for k, v in previous.items() if k != "references"}) != semantic_entity(
                {k: v for k, v in patch.items() if k != "references"}
            ):
                return None
            if patch.get("id"):
                # A patch that removes references must not merge with another patch for that claim.
                original = next(
                    c for claims in plan.target["claims"].values() for c in claims if c["id"] == patch["id"]
                )
                before = [semantic_entity(ref) for ref in original.get("references", [])]
                if any(
                    ref not in [semantic_entity(r) for r in candidate.get("references", [])]
                    for ref in before for candidate in (patch, previous)
                ):
                    return None
            if patch.get("references"):
                combined = deepcopy(previous)
                references = combined.setdefault("references", [])
                for reference in patch["references"]:
                    if semantic_entity(reference) not in [semantic_entity(ref) for ref in references]:
                        references.append(reference)
                patches[key] = combined
    return PlannedEdit(group[0].target, list(patches.values()),
                       [case_id for plan in group for case_id in plan.case_ids], revisions,
                       en_label=next(iter(labels), None), en_description=next(iter(descriptions), None))


async def confirm_corpus_plans(plans, inventory, *, check, progress, uncertain):
    groups = defaultdict(list)
    for plan in plans:
        groups[plan.target["id"]].append(plan)
    reader = ReferenceItemReader()

    async def creator(qid):
        try:
            return await reader.creator_uid(qid)
        except (ValueError, RuntimeError):
            return None

    confirmed = []
    qids = sorted(groups)
    for offset in range(0, len(qids), 8):
        await check()
        await progress("corpus_ownership", offset, len(qids), "items", "Confirm the first creator of each proposed item.")
        window = qids[offset:offset + 8]
        creators = await asyncio.gather(*(creator(qid) for qid in window))
        for qid, uid in zip(window, creators, strict=True):
            group = groups[qid]
            merged = _merge_group(group) if uid == GILA_CREATOR_UID and qid not in uncertain else None
            if uid is None:
                status, reason = "needs_review", "first_creator_unavailable"
            elif uid != GILA_CREATOR_UID:
                status, reason = "other_creator", "item_has_other_creator"
            elif qid in uncertain:
                status, reason = "needs_review", "prior_write_requires_recovery"
            elif merged is None:
                status, reason = "needs_review", "conflicting_claim_or_revision"
            else:
                status, reason = "eligible", "exact_source_and_creator_verified"
                confirmed.append(merged)
            for plan in group:
                for case_id in plan.case_ids:
                    inventory[case_id].update(qid=qid, status=status, reason=reason)
    return confirmed
