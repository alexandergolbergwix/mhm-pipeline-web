from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.pipeline import wikidata_gila_corpus_repair as corpus
from app.pipeline.wikidata_gila_people_batch import PlannedEdit, repair_claim_builder, verify_claims
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
