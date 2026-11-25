from copy import deepcopy

from app.pipeline.wikidata_gila_contents import contents_passage_coverage
from app.pipeline.wikidata_gila_corpus_repair import (
    contents_corpus_inventory,
    source_additions_only,
    source_patches,
)
from app.pipeline.wikidata_gila_facts import compare_gila_source_facts
from app.pipeline.wikidata_gila_people_batch import PlannedEdit, repair_claim_builder
from app.pipeline.wikidata_source_coverage import source_passages

CN = "990000403370205171"
TITLE = "ספר הדרושים"
AUTHOR = "משה בן יהודה"


def field(tag, text, *, indicator=" "):
    return {"tag": tag, "indicators": [" ", indicator], "subfields": [{"code": "a", "value": text}]}


def snak(value):
    return {"snaktype": "value", "datavalue": {"value": value}}


def claim(value, **kwargs):
    return {"mainsnak": snak(value), "rank": "normal", **kwargs}


def manuscript():
    return {"id": "Q100", "lastrevid": 42, "claims": {"P3959": [claim(CN)]}}


def work(qid="Q200", *, control=CN, author=AUTHOR):
    return {"id": qid, "type": "item", "lastrevid": 17, "claims": {
        "P31": [claim({"id": "Q47461344"})],
        "P1476": [claim({"text": TITLE}, references=[{"snaks": {"P3959": [snak(control)]}}])],
        "P2093": [claim(author)],
    }}


def contents_fact(target, works, *, full=True):
    source = {"001": CN, "marc_occurrences": [
        {"tag": "001", "data": CN}, field("100", AUTHOR), field("245", TITLE),
    ]}
    items = [{"entity_type": "work", "existing_qid": row["id"], "records": [CN]} for row in works]
    report = compare_gila_source_facts(
        [source], items, qids_by_cn={CN: "Q100"},
        live_entities={row["id"]: row for row in [target, *works]},
        full_marc_by_cn={CN: source} if full else None,
    )
    return next(row for row in report["facts"] if row["property"] == "P1574")


def test_contents_reconciliation_adds_an_exact_work_and_credits_the_same_case():
    target, exact = manuscript(), work()
    version = contents_fact(target, [exact], full=False)
    fact = contents_fact(target, [exact])
    assert fact["fact_id"] == version["fact_id"]
    assert fact["live_status"] == "missing"
    assert fact["source_repair"]["dependencies"] == {"Q200": 17}

    patches = source_patches({**fact, "control_number": CN}, target, repair_claim_builder())
    assert source_additions_only(PlannedEdit(target, patches, [], {}), allow_folio=True)
    assert len(patches) == 1
    fresh = deepcopy(target)
    fresh["lastrevid"] += 1
    fresh["claims"]["P1574"] = [{**patches[0], "id": "Q100$work"}]
    verified = contents_fact(fresh, [exact])
    assert verified["fact_id"] == fact["fact_id"]
    assert verified["live_status"] == "present"
    assert verified["live_provenance"] == "source_bound"
    assert verified["verified_matches"] == [{"statement_id": "Q100$work", "value": "Q200"}]
    assert "source_repair" not in verified


def test_contents_reconciliation_rejects_other_sources_authors_duplicates_and_conflicts():
    target, exact = manuscript(), work()
    assert "source_repair" not in contents_fact(target, [exact], full=False)
    assert "source_repair" not in contents_fact(target, [work(control="990001")])
    assert "source_repair" not in contents_fact(target, [work(author="משה בן דוד")])
    assert "source_repair" not in contents_fact(target, [exact, work("Q201")])
    target["claims"]["P1574"] = [
        claim({"id": "Q300"}, id="Q100$other", qualifiers={"P1932": [snak(TITLE)]}),
    ]
    assert "source_repair" not in contents_fact(target, [exact])


def test_contents_preflight_verifies_an_existing_shared_work_with_an_exact_author():
    target, shared = manuscript(), work()
    shared["claims"].pop("P1476")
    shared["claims"].pop("P2093")
    shared["claims"]["P50"] = [claim({"id": "Q300"})]
    shared["labels"] = {"he": {"value": TITLE}}
    source = {"001": CN, "marc_occurrences": [
        {"tag": "001", "data": CN}, field("245", TITLE),
        {"tag": "100", "subfields": [
            {"code": "a", "value": AUTHOR}, {"code": "d", "value": "1420-1480"},
        ]},
    ]}
    person = {"id": "Q300", "lastrevid": 18, "labels": {"he": {"value": AUTHOR}}, "claims": {
        "P31": [claim({"id": "Q5"})],
        "P569": [claim({"precision": 9, "time": "+1420-00-00T00:00:00Z"})],
        "P570": [claim({"precision": 9, "time": "+1480-00-00T00:00:00Z"})],
    }}
    target["claims"]["P1574"] = [claim(
        {"id": "Q200"}, id="Q100$work",
        qualifiers={"P1932": [snak(TITLE)], "P1480": [snak({"id": "Q18122778"})]},
        references=[{"snaks": {"P3959": [snak(CN)]}}],
    )]
    def compare():
        return next(row for row in compare_gila_source_facts(
            [source], [], qids_by_cn={CN: "Q100"}, full_marc_by_cn={CN: source},
            live_entities={row["id"]: row for row in (target, shared, person)},
        )["facts"] if row["property"] == "P1574")

    before = deepcopy(target)
    verified = compare()
    assert verified["verified_matches"] == [{"statement_id": "Q100$work", "value": "Q200"}]
    assert "source_repair" not in verified and target == before
    person["claims"]["P570"] = [claim({"precision": 9, "time": "+1482-00-00T00:00:00Z"})]
    assert not compare()["verified_matches"]


def test_passage_coverage_requires_an_exact_scope_in_addition_to_a_public_title():
    bare = field("740", TITLE)
    analytic = field("740", "ספר התשובות", indicator="2")
    structured = field("505", "ספר התפילות")
    record = {"001": CN, "marc_occurrences": [{"tag": "001", "data": CN}, bare, analytic, structured]}
    flags = [{
        "property": "P1574", "control_number": CN, "qid": "Q100",
        "expected_qualifiers": {"P1932": title, "P958": "א-ב"},
        "matches": [{"statement_id": f"Q100${index}"}],
    } for index, title in enumerate([TITLE, "ספר התשובות", "ספר התפילות"])]
    passages = [{**row, "qid": "Q100"} for row in source_passages(record)]
    assert contents_passage_coverage(passages, flags, {CN: record}) == {
        "740": {"total": 2, "title_matches": 2, "with_folios": 2, "verified": 1},
        "505": {"total": 1, "title_matches": 1, "with_folios": 1, "verified": 1},
    }
    flags[1]["qid"] = "Q999"
    assert contents_passage_coverage(passages, flags, {CN: record})["740"]["verified"] == 0
    flags[2]["expected_qualifiers"].pop("P958")
    assert contents_passage_coverage(passages, flags, {CN: record})["505"]["verified"] == 0


def test_contents_preflight_keeps_old_cases_and_excludes_passages():
    rows = [
        {"case_id": "current", "points": [6], "reason": "identity", "live_status": "identity_review"},
        {"case_id": "passage:one", "points": [3, 6]},
        {"case_id": "other", "points": [1]},
    ]
    prior = [{"case_id": "closed", "points": [6]}, {"case_id": "disappeared", "points": [6]}]
    inventory = contents_corpus_inventory(rows, prior, {"closed"})
    assert {key: row["status"] for key, row in inventory.items()} == {
        "current": "needs_review", "closed": "already_verified", "disappeared": "needs_review",
    }
