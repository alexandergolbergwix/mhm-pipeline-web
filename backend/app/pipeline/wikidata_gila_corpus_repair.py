"""Prepare a complete, creator-checked repair plan before the first write."""

import asyncio
from collections import defaultdict
from copy import deepcopy

from app.pipeline.wikidata_gila_execution import repair_claims, semantic_entity
from app.pipeline.wikidata_gila_people_batch import PlannedEdit, _load_items, repair_claim_builder
from app.pipeline.wikidata_gila_repairs import build_gila_repair_proposals, validate_gila_repair_proposal
from app.pipeline.wikidata_gila_sources import matching_claims, source_colophon
from app.pipeline.wikidata_reference_reader import ReferenceItemReader
from app.pipeline.wikidata_source_choices import _claim_values
from app.publication.reviewed_reference_scope import GILA_CREATOR_UID
from converter.wikidata.item_models import WikidataStatement
from converter.wikidata.property_mapping import Q_KTIV, nli_catalog_url


def corpus_inventory(cases):
    return {
        case["case_id"]: {
            "case_id": case["case_id"],
            "points": [point for point in case["points"] if point in {5, 8}],
            "qid": case["qid"] if "qid" in case else None,
            "control_number": case.get("control_number"),
            "source_field": case.get("source_field"),
            "reason": case.get("completion_reason") or case.get("reason") or "source_evidence_required",
            "status": "other_creator" if case.get("completion_reason") == "item_has_other_creator" else "needs_evidence",
        }
        for case in cases if {5, 8}.intersection(case["points"])
        and not case["case_id"].startswith("passage:")
    }


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
    ]
    qualifiers = [
        {"property": prop, "value": value, "type": "item" if prop == "P3831" else "string"}
        for prop, value in proof["qualifiers"].items()
    ]
    patch = build_claim(WikidataStatement(
        pid, proof["value"], proof["type"], qualifiers=qualifiers,
        references=reference, reference_blocks=[reference], language=proof["language"] or "he",
    ))
    matches = matching_claims(target, pid, proof["value"], proof["qualifiers"], language=proof["language"])
    if not matches:
        return patch
    claim_id = matches[0]["claim_id"]
    original = deepcopy(next(row for row in target["claims"][pid] if row["id"] == claim_id))
    original.setdefault("references", []).extend(patch["references"])
    return original


def _exact_colophon(target, records):
    codes = set(_claim_values(target, "P3959"))
    if len(codes) == 1:
        return source_colophon(records.get(next(iter(codes))))
    return ""


async def plan_sources(cases, records, *, check, progress, inventory):
    proposals = [c for c in cases if c.get("source_repair") or c["case_id"].startswith("historical:")]
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
        if proof:
            patches = [_source_patch(case, target, build_claim)]
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
        plans.append(PlannedEdit(target, patches, [case_id], dependencies))
    return plans


def _patch_key(patch):
    if patch.get("id"):
        return patch["id"]
    return tuple(repr(semantic_entity(patch.get(key))) for key in ("mainsnak", "qualifiers", "rank"))


def _merge_group(group):
    patches = {}
    revisions = {}
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
                       [case_id for plan in group for case_id in plan.case_ids], revisions)


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
