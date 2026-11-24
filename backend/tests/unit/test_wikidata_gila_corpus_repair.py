from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.pipeline import wikidata_gila_corpus_repair as corpus
from app.pipeline.wikidata_gila_audit import current_holding_label
from app.pipeline.wikidata_gila_authority_cache import AuthorityEvidence
from app.pipeline.wikidata_gila_people_batch import PersonRepairGateway, PlannedEdit, repair_claim_builder, verify_claims
from app.pipeline.wikidata_gila_people_evidence_batch import evidence_batch, keep_reviewed_targets
from app.pipeline.wikidata_gila_sources import full_source_cases
from app.publication.reviewed_reference_scope import GILA_CREATOR_UID

CN = "990000403370205171"


def source():
    return {
        "001": CN, "marc_occurrences": [
            {"tag": "957", "subfields": [{"code": "a", "value": 'קולופון (דף ב): "ונשלמה המלאכה"'}]},
            {"tag": "590", "subfields": [{"code": "a", "value": "רשומה זמנית"}]},
            *({"tag": "581", "subfields": [{"code": "a", "value": "Catalogue, 1886"}]} for _ in range(2)),
        ],
    }


def item(qid="Q1"):
    identity = {"datavalue": {"value": CN}, "property": "P3959", "snaktype": "value"}
    return {
        "id": qid, "lastrevid": 42, "claims": {
            "P3959": [{"id": f"{qid}$source", "mainsnak": identity}],
        },
        "labels": {}, "descriptions": {}, "aliases": {}, "sitelinks": {},
    }


def inscription(target):
    rows = list(full_source_cases(CN, source(), {}, target, {}, {}))
    assert len(rows) == len({row["occurrence"] for row in rows}) == 4
    assert sum(row["reason"] == "bibliographic_source_identity_unresolved" for row in rows) == 2
    assert any(row["reason"] == "catalogue_status_evidence" for row in rows)
    return {**rows[0], "case_id": f"source:{CN}:evidence:957:1:1", "control_number": CN, "points": [8]}


def test_exact_marc_inscription_keeps_the_role_folio_and_case_id():
    target = item()
    case = inscription(target)
    assert case["source_repair"]["qualifiers"] == {"P3831": "Q372474", "P7416": "ב"}
    patch = corpus._source_patch(case, target, repair_claim_builder())
    fresh = deepcopy(target)
    fresh["claims"]["P1684"] = [{**patch, "id": "Q1$inscription"}]
    fresh["lastrevid"] += 1
    assert verify_claims(target, fresh, fresh["lastrevid"], [patch])
    assert inscription(fresh)["verified"]
    fresh["claims"]["P1684"][0]["rank"] = "deprecated"
    assert inscription(fresh)["reason"] == "ambiguous_source_claim"


@pytest.mark.asyncio
async def test_corpus_preflight_blocks_other_creators_and_merges_the_same_source_claim(monkeypatch):
    read = AsyncMock(side_effect=[GILA_CREATOR_UID, 999])
    monkeypatch.setattr(corpus.ReferenceItemReader, "creator_uid", read)
    plans = []
    cases = []
    for qid in ("Q1", "Q2"):
        target = item(qid)
        case = {**inscription(target), "case_id": qid, "qid": qid}
        cases.append(case)
        patch = corpus._source_patch(case, target, repair_claim_builder())
        plans.append(PlannedEdit(target, [patch], [qid], {qid: 42}))
    cases.append({**cases[0], "case_id": "extra"})
    plans.append(PlannedEdit(plans[0].target, plans[0].patches, ["extra"], {"Q1": 42}))
    inventory = corpus.corpus_inventory(cases)
    confirmed = await corpus.confirm_corpus_plans(
        plans, inventory, check=AsyncMock(), progress=AsyncMock(), uncertain=set(),
    )
    assert len(confirmed) == len(confirmed[0].patches) == 1
    assert confirmed[0].case_ids == ["Q1", "extra"]
    assert inventory["Q2"]["status"] == "other_creator"
    assert inventory["Q1"]["status"] == "eligible"


def test_inscription_variant_with_the_same_scope_does_not_make_another_claim():
    target = item()
    quotation = inscription(target)
    patch = corpus._source_patch(quotation, target, repair_claim_builder())
    patch["id"] = "Q1$earlier"
    patch["mainsnak"]["datavalue"]["value"]["text"] = "ונשלמה המלאכה בשבת"
    target["claims"]["P1684"] = [patch]
    review = inscription(target)
    assert review["reason"] == "inscription_text_overlap"
    assert "source_repair" not in review


def test_distinct_source_transcriptions_without_separate_folios_require_review():
    record = source()
    record["marc_occurrences"].append({
        "tag": "957", "subfields": [{"code": "a", "value": 'קולופון: "כאן נשלמה המלאכה"'}],
    })
    rows = list(full_source_cases(CN, record, {}, item(), {}, {}))
    reviews = [row for row in rows if row["occurrence"].startswith("957:")]
    assert len(reviews) == 2
    assert all(row["reason"] == "inscription_text_overlap" and "source_repair" not in row for row in reviews)


def test_people_evidence_batch_preserves_all_decisions_and_rejects_target_changes():
    selected, inventory, _ = evidence_batch([])
    assert not selected
    assert Counter(row["person_role"] for row in inventory.values()) == {"author": 84, "scribe": 39, "former owner": 91}
    assert all(row["reason"] == "case_absent_from_current_open_audit" for row in inventory.values())
    case_id = next(iter(inventory))
    decision = inventory[case_id]
    case = {**decision, "qid": "Q2", "occurrence": decision["source_field"]}
    selected, inventory, _ = evidence_batch([case, {**case, "case_id": "unrelated"}])
    assert not selected
    assert inventory[case_id]["reason"] == "source_occurrence_changed"
    assert not keep_reviewed_targets([PlannedEdit(target=item("Q2"), patches=[], case_ids=[case_id], revisions={})], inventory)
    assert inventory[case_id]["reason"] == "reviewed_target_changed"


@pytest.mark.asyncio
async def test_authority_cache_reuses_exact_reads_and_retries_missing_evidence():
    cache = AuthorityEvidence("original")
    keys = frozenset({("P214", "123")})
    fetch = AsyncMock(return_value={"A": keys})
    assert await cache.read("viaf", {"A", "B"}, fetch, None) == {"A": keys}
    snapshot = cache.dump()
    resumed = AuthorityEvidence.load(snapshot, "original")
    fetch.reset_mock()
    fetch.return_value = {"B": keys}
    assert await resumed.read("viaf", {"A", "B"}, fetch, None) == {"A": keys, "B": keys}
    assert fetch.call_args.args[0] == {"B"}
    assert resumed.reused == 1
    assert AuthorityEvidence.load(snapshot, "changed").viaf == {}
    snapshot["observed_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    assert AuthorityEvidence.load(snapshot, "original").viaf == {}


def test_a_label_patch_keeps_other_labels_and_claims():
    before = item()
    before["labels"]["he"] = {"language": "he", "value": "ספר"}
    after = deepcopy(before)
    after["lastrevid"] += 1
    after["labels"]["en"] = {"language": "en", "value": "Library. Ms. 5"}
    assert verify_claims(before, after, 43, [], en_label="Library. Ms. 5")
    after["labels"].pop("he")
    assert not verify_claims(before, after, 43, [], en_label="Library. Ms. 5")
    payload = PersonRepairGateway(None)._write_parameters("Q1", 42, [], "fixture", en_label="Library. Ms. 5")
    assert payload["baserevid"] == 42
    assert payload["data"] == '{"labels": {"en": {"value": "Library. Ms. 5", "language": "en"}}}'


@pytest.mark.asyncio
async def test_holding_label_requires_the_original_occurrence_and_current_shelfmark(monkeypatch):
    full = {
        "001": CN,
        "942$a": "Zu\u0308rich Library",
        "942$z": "Ms. 5",
        "marc_occurrences": [
            {"tag": "001", "data": CN},
            {"tag": "942", "indicators": [" ", " "],
             "subfields": [{"code": "a", "value": "Zu\u0308rich Library"}, {"code": "z", "value": "Ms. 5"}]},
        ],
    }
    target = item()
    target["claims"]["P217"] = [{
        "id": "Q1$shelfmark", "rank": "normal",
        "mainsnak": {"snaktype": "value", "datavalue": {"value": "Ms. 5"}},
        "references": [{"snaks": {"P3959": [target["claims"]["P3959"][0]["mainsnak"]]}}],
    }]
    label = current_holding_label(CN, full, target)
    assert label == "Zürich Library, Ms. 5"
    case = {
        "case_id": "source:holding", "points": [1], "qid": "Q1",
        "control_number": CN, "holding_label": label, "live_revision": 42,
    }
    inventory = corpus.corpus_inventory([case], frozenset({1, 5}))

    async def load(reader, qids, items, check):
        items["Q1"] = target

    monkeypatch.setattr(corpus, "_load_items", load)
    plans = await corpus.plan_holding_labels([case], {CN: full}, check=AsyncMock(), inventory=inventory)
    assert len(plans) == 1
    assert plans[0].en_label == label
    target["claims"]["P217"].append({
        "rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"value": "Ms. 8"}},
    })
    assert not await corpus.plan_holding_labels([case], {CN: full}, check=AsyncMock(), inventory=inventory)
    assert inventory["source:holding"]["status"] == "needs_review"
