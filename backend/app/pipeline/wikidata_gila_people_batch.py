"""Plan and verify source-bound person edits on existing project items."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass

from app.pipeline import marc_occurrences as marc
from app.pipeline.marc_ingest import prepare_record_for_pipeline
from app.pipeline.wikidata_gila_authority_reads import mazal_authority_url, read_named_mazal_people
from app.pipeline.wikidata_gila_bibframe_people import frame_field_ids, read_bibframes
from app.pipeline.wikidata_gila_execution import LiveRepairGateway, WriteRejectedError, semantic_entity
from app.pipeline.wikidata_gila_facts import _live_claims, _reference_status
from app.pipeline.wikidata_gila_linked_people import (
    ROLE_PROPERTIES,
    _eligible_role,
    has_explicit_linked_heading,
    has_linked_source,
    linked_person_matches,
    role_authority,
    read_linked_bibs,
)
from app.pipeline.wikidata_gila_mazal_viaf import (
    authority_items, read_viaf_mazal, viaf_matches, viaf_mazal_url,
)
from app.pipeline.wikidata_gila_people import (
    _author_target,
    _exact_life_dates,
    _heading,
    _person_occurrences,
    _ROLE_QUALIFIERS,
    _qualifier_values,
    _separate_scribe_claim,
    _sole_work_for_added_author,
)
from app.pipeline.wikidata_gila_scribe_colophons import reviewed_scribe_exclusion, reviewed_scribe_source
from app.pipeline.wikidata_reference_reader import ReferenceItemReader
from app.pipeline.wikidata_source_choices import _claim_values
from app.publication.reviewed_reference_scope import GILA_CREATOR_NAME, GILA_CREATOR_UID
from app.publication.wikidata_gateway import CurrentWikidataBoundaryFactory
from converter.wikidata.item_models import WikidataStatement
from converter.wikidata.property_mapping import Q_KTIV
from converter.wikidata.retry_policy import STUDIO_MAXLAG

logger = logging.getLogger(__name__)
_RETRYABLE = {"maxlag", "ratelimited", "readonly"}


@dataclass(frozen=True)
class PersonCase:
    case_id: str
    control: str
    manuscript: str
    role: str
    heading: str
    authority: str
    source_url: str
    record: Mapping
    field: dict
    variants: list[dict]
    occurrences: list[tuple[dict, list[dict]]]
    dated_authority: bool = False


def _source_fields(cases, records):
    rows = {}
    for case in cases:
        cn = case["control_number"]
        if cn not in records:
            continue
        if cn not in rows:
            record = prepare_record_for_pipeline(dict(records[cn]))
            occurrences = _person_occurrences(record)
            rows[cn] = (record, occurrences, {marc.source_key(row): (row, vs) for row, vs in occurrences})
        record, occurrences, fields = rows[cn]
        key, role, display = case["occurrence"], case["person_role"], case["source_value"]
        if key not in fields:
            continue
        field, variants = fields[key]
        if [name.strip().rstrip(",") for name in marc.values(field, "a")] != [display]:
            continue
        if role == "scribe" and (
            reviewed_scribe_exclusion(record, cn, key, display)
            or reviewed_scribe_source(record, cn, key, display)
        ):
            continue
        yield case, cn, role, display, record, field, variants, occurrences


def source_cases(cases, records, bibs, frames=None):
    frames = frames or {}
    for case, cn, role, display, record, field, variants, occurrences in _source_fields(cases, records):
        if proof := role_authority(
            cn, field, variants, occurrences, role, record=record, bibs=bibs, frames=frames,
        ):
            aid, source_url = proof
            yield PersonCase(
                case["case_id"], cn, case["qid"], role, display, aid, source_url, record, field, variants, occurrences,
            )


async def dated_source_cases(cases, records, bibs, frames):
    drafted = []
    for case, cn, role, display, record, field, variants, occurrences in _source_fields(cases, records):
        if role not in {"author", "scribe", "mentioned"} or not _eligible_role(field, variants, role):
            continue
        years = _exact_life_dates(variants)
        if (
            not years or has_explicit_linked_heading(variants, bibs.get(cn, {}))
            or frame_field_ids(field, frames.get(cn, {})) - {""}
        ):
            continue
        names = {_heading(value) for variant in variants for value in marc.values(variant, "a")}
        if any(marc.values(variant, code) for variant in variants for code in ("b", "c", "q")):
            continue
        names = {name for name in names if len(name.split()) > 1}
        if names:
            drafted.append((case, cn, role, display, record, field, variants, occurrences, names, years))
    index = await read_named_mazal_people({name for row in drafted for name in row[-2]})
    results = []
    for case, cn, role, display, record, field, variants, occurrences, names, years in drafted:
        ids = {aid for name in names for aid in index.get((name, *years), ())}
        if len(ids) == 1:
            aid = next(iter(ids))
            results.append(PersonCase(
                case["case_id"], cn, case["qid"], role, display, aid, mazal_authority_url(aid),
                record, field, variants, occurrences, dated_authority=True,
            ))
    return results


def target_for(case, manuscript, entities):
    if set(_claim_values(manuscript, "P3959")) != {case.control}:
        return {}
    if case.role != "author":
        return manuscript
    if case.field["tag"] != "100" and not marc.values(case.field, "t") and not _sole_work_for_added_author(
        case.field, case.record, manuscript, case.occurrences,
    ):
        return {}
    return _author_target(case.field, case.record, manuscript, entities, case.control)


def scoped_claim(case, person, target, build_claim):
    """Return a single claim patch or a reason the source cannot change it."""
    pid = ROLE_PROPERTIES[case.role]
    qid = person["id"]
    if target["id"] == qid:
        return None, "identity_or_scope"
    matches = []
    expected_role = _ROLE_QUALIFIERS.get(case.role)
    names = {_heading(value) for variant in case.variants for value in marc.values(variant, "a")}
    for claim in _live_claims(target, pid):
        role = _qualifier_values(claim, "P3831")
        value = claim["value"]
        if pid == "P3342" and role != ([expected_role] if expected_role else []):
            if expected_role and not role and value == qid:
                return None, "conflicting_claim"
            continue
        recorded_names = _qualifier_values(claim, "P1932")
        if value == qid:
            if recorded_names and (len(recorded_names) != 1 or _heading(recorded_names[0]) not in names):
                return None, "conflicting_claim"
            matches.append(claim)
        elif value == "somevalue" and case.role == "scribe" and len(recorded_names) == 1 and (
            _heading(recorded_names[0]) in names
            and {row["property"] for row in claim["qualifiers"]} == {"P1932"}
            and _reference_status([claim], case.control, live=True) == "source_bound"
        ):
            matches.append(claim)
        elif case.role == "author" or (
            case.role == "scribe" and not _separate_scribe_claim(
                claim, case.control, case.field, names, case.occurrences,
            )
        ) or any(_heading(name) in names for name in recorded_names):
            return None, "conflicting_claim"
    if len(matches) > 1:
        return None, "conflicting_claim"
    original = matches[0] if matches else None
    authority_urls = [case.source_url]
    if not linked_person_matches(person, case.authority):
        authority_urls.append(viaf_mazal_url(case.authority))
    if original and original["value"] == qid and has_linked_source(original, case.control, *authority_urls):
        return None, "already_cited"
    if any(
        claim.get("rank") == "deprecated"
        and claim.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id") == qid
        for claim in target["claims"].get(pid, [])
        if isinstance(claim.get("mainsnak", {}).get("datavalue", {}).get("value"), dict)
    ):
        return None, "conflicting_claim"
    reference = [
        {"property": "P248", "value": Q_KTIV, "type": "item", "value_namespace": "wikidata"},
        {"property": "P3959", "value": case.control, "type": "external-id"},
        *({"property": "P854", "value": url, "type": "url"} for url in authority_urls),
    ]
    qualifiers = [{"property": "P1932", "value": case.heading, "type": "string"}]
    if expected_role:
        qualifiers.append({"property": "P3831", "value": expected_role, "type": "item"})
    statement = WikidataStatement(
        pid, qid, "item", reference_blocks=[reference], references=reference, qualifiers=qualifiers,
    )
    patch = build_claim(statement)
    if original:
        if not original["claim_id"]:
            return None, "conflicting_claim"
        existing = deepcopy(next(claim for claim in target["claims"][pid] if claim["id"] == original["claim_id"]))
        if original["value"] == "somevalue":
            existing["mainsnak"] = patch["mainsnak"]
        existing.setdefault("references", []).extend(patch["references"])
        patch = existing
    return patch, None


def apply_claims(entity, patches):
    """Apply exact claim changes to a copy for the independent read-back check."""
    expected = deepcopy(entity)
    old_claim_ids = {claim["id"] for rows in entity["claims"].values() for claim in rows if "id" in claim}
    additions = []
    for patch in patches:
        if "remove" in patch:
            pid = next(pid for pid, rows in expected["claims"].items() if any(c["id"] == patch["id"] for c in rows))
            expected["claims"][pid] = [c for c in expected["claims"][pid] if c["id"] != patch["id"]]
            if not expected["claims"][pid]:
                del expected["claims"][pid]
            continue
        rows = expected["claims"].setdefault(patch["mainsnak"]["property"], [])
        if patch.get("id"):
            index = next(i for i, claim in enumerate(rows) if claim["id"] == patch["id"])
            rows[index] = patch
        else:
            addition = deepcopy(patch)
            rows.append(addition)
            additions.append(addition)
    return expected, additions, old_claim_ids


def verify_claims(before, after, revision, patches):
    if revision <= before["lastrevid"] or after.get("lastrevid") != revision:
        return False
    expected, additions, old_ids = apply_claims(before, patches)
    for added in additions:
        prop = added["mainsnak"]["property"]
        matches = [
            claim["id"] for claim in after["claims"].get(prop, [])
            if claim.get("id") and claim["id"] not in old_ids
            and semantic_entity({key: value for key, value in claim.items() if key != "id"}) == semantic_entity(added)
        ]
        if len(matches) != 1:
            return False
        added["id"] = matches[0]
        old_ids.add(matches[0])
    return semantic_entity(after) == semantic_entity(expected)


class PersonRepairGateway(LiveRepairGateway):
    def _write_parameters(self, qid, revision, claims, token):
        data = super()._write_parameters(qid, revision, claims, token)
        return {
            **data, "assertuser": GILA_CREATOR_NAME,
            "summary": "Repair verified manuscript claims from NLI MARC and exact source evidence",
        }


async def _load_items(reader, qids, entities, check):
    pending = sorted(set(qids) - entities.keys())
    async with reader._public_client() as client:
        for offset in range(0, len(pending), 50):
            await check()
            entities.update(await reader.fetch_batch(client, pending[offset:offset + 50]))


def _item_ids(entity, prop):
    return {value["id"] for value in _claim_values(entity, prop) if isinstance(value, dict) and value.get("id")}


def plan_groups(proofs, candidates, entities, counts, viaf=None):
    groups = defaultdict(list)
    for case in proofs:
        person = entities.get(candidates.get(case.authority), {})
        target = target_for(case, entities.get(case.manuscript, {}), entities)
        if not linked_person_matches(person, case.authority) and (
            case.dated_authority or not viaf_matches(person, case.authority, (viaf or {}).get(case.authority))
        ):
            counts["no_exact_person"] += 1
        elif not target:
            counts["no_exact_target"] += 1
        else:
            groups[target["id"]].append((case, person))
    return groups


def patches_for_group(group, target, build_claim, counts):
    """Combine sources for the same person claim and retain all case IDs."""
    patches = {}
    case_ids = defaultdict(list)
    conflicts = set()
    for case, person in group:
        patch, reason = scoped_claim(case, person, target, build_claim)
        if reason:
            counts[reason] += 1
            continue
        prop = patch["mainsnak"]["property"]
        key = patch.get("id") or (prop, person["id"], _ROLE_QUALIFIERS.get(case.role))
        if key in patches:
            prior = patches[key]
            if semantic_entity(prior["mainsnak"]) != semantic_entity(patch["mainsnak"]):
                conflicts.add(key)
            for ref in patch["references"]:
                if semantic_entity(ref) not in [semantic_entity(row) for row in prior["references"]]:
                    prior["references"].append(ref)
        else:
            patches[key] = patch
        case_ids[key].append(case.case_id)
    new_authors = {key for key in patches if isinstance(key, tuple) and key[0] == "P50"}
    if len(new_authors) > 1:
        conflicts.update(new_authors)
    for key in conflicts:
        counts["conflicting_claim"] += len(case_ids.pop(key))
        del patches[key]
    return list(patches.values()), [case_id for ids in case_ids.values() for case_id in ids]


@dataclass
class PlannedEdit:
    target: dict
    patches: list[dict]
    case_ids: list[str]
    revisions: dict[str, int]


def repair_claim_builder():
    from converter.wikidata.uploader import WikidataUploader

    # Native serialization needs only the main-wiki data types. A preflight has no login.
    builder = WikidataUploader.__new__(WikidataUploader)
    builder._is_test = False
    return lambda statement: builder._build_claim(statement).get_json()


async def plan_people_batch(cases, records, *, check, progress, counts):
    """Resolve exact roles and prepare revision-bound patches without a login."""
    eligible_cases = [
        case for case in cases
        if case.get("points") == [5] and case.get("person_role") in ROLE_PROPERTIES
        and all(case.get(key) for key in ("qid", "source_value", "control_number", "occurrence"))
    ]
    counts.update(open_cases=sum(case.get("points") == [5] and bool(case.get("person_role")) for case in cases))
    controls = {case["control_number"] for case in eligible_cases}
    counts["supported_roles"] = len(eligible_cases)

    async def read_progress(processed, total):
        await check()
        await progress("people_evidence", processed, total, "records", "Read NLI authority links for open person cases.")

    bibs = await read_linked_bibs(controls, read_progress)
    counts["unavailable_catalogues"] = len(controls - bibs.keys())
    direct_proofs = list(source_cases(eligible_cases, records, bibs))
    direct_keys = {case.case_id for case in direct_proofs}
    remaining = [case for case in eligible_cases if case["case_id"] not in direct_keys]

    async def frame_progress(processed, total):
        await check()
        await progress("people_bibframe", processed, total, "records",
                       "Match original MARC fields to NLI BIBFRAME agents.")

    frames = await read_bibframes({case["control_number"] for case in remaining}, frame_progress)
    proofs = [*direct_proofs, *source_cases(remaining, records, bibs, frames)]
    counts["bibframe_matches"] = len(proofs) - len(direct_proofs)
    proven_ids = {case.case_id for case in proofs}
    dated = await dated_source_cases([case for case in remaining if case["case_id"] not in proven_ids],
                                     records, bibs, frames)
    counts["dated_matches"] = len(dated)
    proofs.extend(dated)
    counts["source_matches"] = len(proofs)
    if not proofs:
        return []
    await progress("people_plan", 0, len(proofs), "cases", "Match NLI authority IDs to existing Wikidata people.")
    identifiers = {case.authority for case in proofs}
    direct = await asyncio.to_thread(authority_items, {("P8189", aid) for aid in identifiers})

    async def viaf_progress(processed, total):
        await check()
        await progress("people_viaf", processed, total, "identities",
                       "Check exact VIAF cross-references for NLI authorities.")

    viaf = await read_viaf_mazal({
        case.authority for case in proofs if not case.dated_authority and not direct.get(("P8189", case.authority))
    }, viaf_progress)
    alternatives = await asyncio.to_thread(authority_items, {key for keys in viaf.values() for key in keys})
    candidates = {}
    for aid in identifiers:
        matches = direct.get(("P8189", aid)) or {
            qid for key in viaf.get(aid, ()) for qid in alternatives.get(key, ())
        }
        if len(matches) == 1:
            candidates[aid] = next(iter(matches))
    reader = ReferenceItemReader()
    entities = {}
    await _load_items(reader, {case.manuscript for case in proofs}, entities, check)
    await _load_items(reader, {candidate for candidate in candidates.values()} | {
        qid for case in proofs if case.role == "author"
        for qid in _item_ids(entities.get(case.manuscript, {}), "P1574")
    }, entities, check)
    groups = plan_groups(proofs, candidates, entities, counts, viaf)
    counts["target_items"] = len(groups)
    plans = []
    build_claim = repair_claim_builder()
    for qid, group in sorted(groups.items()):
        before = entities[qid]
        patches, case_ids = patches_for_group(group, before, build_claim, counts)
        if not patches:
            continue
        sources = {qid}
        for case, person in group:
            if case.case_id in case_ids:
                sources.update({case.manuscript, person["id"]})
        plans.append(PlannedEdit(before, patches, case_ids, {key: entities[key]["lastrevid"] for key in sources}))
    return plans


async def apply_planned_edits(
    plans, *, credential, check, check_source, progress, journal, counts, uncertain_qids,
):
    """Apply only the saved claims and prove each resulting entity."""
    if not plans:
        return []
    await check_source()
    reader = ReferenceItemReader()
    boundary = await CurrentWikidataBoundaryFactory().open(await credential())
    uploader = boundary._uploader
    identity = await asyncio.to_thread(uploader._query_userinfo_rights)
    if not isinstance(identity, dict) or identity.get("id") != GILA_CREATOR_UID:
        raise ValueError("The production credential does not match the project creator.")
    gateway = PersonRepairGateway(uploader, read_item=reader.fetch, maxlag=STUDIO_MAXLAG)
    verified = []
    written = {}
    for index, plan in enumerate(plans):
        before, patches, case_ids = plan.target, plan.patches, plan.case_ids
        qid = before["id"]
        await check()
        await progress("corpus_apply", index, len(plans), "items",
                       f"Check {qid}. Verified {counts['edited_items']} project items.")
        if qid in uncertain_qids:
            counts["prior_uncertain"] += len(case_ids)
            continue
        try:
            if await reader.creator_uid(qid) != GILA_CREATOR_UID:
                counts["excluded_creator"] += len(case_ids)
                continue
            await gateway.assert_allowed(qid)
            async with reader._public_client() as client:
                sources = sorted(plan.revisions)
                latest = {}
                for start in range(0, len(sources), 50):
                    latest.update(await reader.fetch_batch(client, sources[start:start + 50]))
                if any(
                    latest.get(key, {}).get("lastrevid")
                    != (written.get(key, plan.revisions[key]) if key != qid else plan.revisions[key])
                    for key in sources
                ):
                    counts["changed_revision"] += len(case_ids)
                    continue
        except (ValueError, RuntimeError) as error:
            logger.warning("Person repair could not confirm %s (%s).", qid, type(error).__name__)
            counts["unavailable_targets"] += len(case_ids)
            continue

        revision = before["lastrevid"]
        receipt = {"qid": qid, "revision": revision, "case_ids": case_ids, "claims": patches}
        updated = None
        for attempt in range(3):
            if attempt:
                await check()
                try:
                    await gateway.assert_allowed(qid)
                    if any([await reader.current_revision(key) != latest[key]["lastrevid"] for key in sources]):
                        counts["changed_revision"] += len(case_ids)
                        break
                except (ValueError, RuntimeError):
                    counts["unavailable_targets"] += len(case_ids)
                    break
            await journal("intent", receipt)
            logger.info("Person repair intent %s at revision %s for %s cases.", qid, revision, len(case_ids))
            try:
                updated = await gateway.write(qid, revision, patches)
                break
            except WriteRejectedError as error:
                await journal("rejected", {**receipt, "code": error.code})
                if error.code not in _RETRYABLE:
                    counts["rejected"] += len(case_ids)
                    break
                wait = max(15, error.retry_after_seconds or 0)
                if attempt == 2 or wait > 105:
                    counts["service_pause"] += 1
                    logger.warning("Wikidata requested a pause for person repairs (%s).", error.code)
                    break
                await progress("corpus_apply", index, len(plans), "items",
                               f"Wikidata requested a {wait:g}-second wait before the edit on {qid}.")
                await asyncio.sleep(wait)
            except Exception:
                await journal("uncertain", receipt)
                counts["uncertain"] += len(case_ids)
                logger.exception("The result of the person edit on %s is uncertain.", qid)
                break
        if counts["service_pause"] or counts["uncertain"]:
            break
        if updated is None:
            continue
        try:
            fresh = await reader.fetch(qid)
            for delay in (2, 4):
                if fresh["lastrevid"] >= updated:
                    break
                await asyncio.sleep(delay)
                fresh = await reader.fetch(qid)
            if not verify_claims(before, fresh, updated, patches):
                raise ValueError(f"The complete item read-back differs for {qid} at revision {updated}.")
        except Exception:
            await journal("uncertain", {**receipt, "after_revision": updated})
            counts["uncertain"] += len(case_ids)
            logger.exception("The complete read-back of the person edit on %s is uncertain.", qid)
            break
        await journal("verified", {
            "qid": qid, "revision": revision, "case_ids": case_ids, "after_revision": updated,
        })
        logger.info("Person repair verified %s at revision %s for %s cases.", qid, updated, len(case_ids))
        counts["edited_items"] += 1
        counts["claims_written"] += len(patches)
        verified.extend(case_ids)
        written[qid] = updated
    await progress("corpus_apply", min(index + 1, len(plans)), len(plans), "items",
                   f"Verified edits on {counts['edited_items']} project items.")
    return verified
