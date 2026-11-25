from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.pipeline import marc_occurrences as marc
from app.pipeline import wikidata_gila_corpus_repair as corpus
from app.pipeline import wikidata_gila_holdings_contents as holdings_contents
from app.pipeline import wikidata_gila_uniform_titles as uniform_titles
from app.pipeline.wikidata_gila_audit import build_gila_audit_report, current_holding_label
from app.pipeline.marc_ingest import prepare_record_for_pipeline
from app.pipeline.wikidata_gila_authority_cache import AuthorityEvidence
from app.pipeline.wikidata_gila_people_batch import PersonRepairGateway, PlannedEdit, repair_claim_builder, verify_claims
from app.pipeline.wikidata_gila_people_evidence_batch import evidence_batch, keep_reviewed_targets
from app.pipeline.wikidata_gila_sources import _reviewed_source_case, full_source_cases
from app.pipeline.wikidata_gila_execution import semantic_entity
from app.pipeline.wikidata_source_choices import digest
from app.publication.reviewed_reference_scope import GILA_CREATOR_UID
from converter.wikidata.property_mapping import nli_catalog_url

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


def test_legacy_citation_reconciles_only_to_one_original_occurrence():
    field = {
        "tag": "581", "occurrence": 1, "indicators": [" ", " "],
        "subfields": [{"code": "a", "value": "Café catalogue; entry 14"}],
    }
    record = {"001": CN, "marc_occurrences": [field]}
    legacy = {"catalog_references": [
        {"source_field": "581", "text": text}
        for text in ("Cafe\u0301 catalogue", "entry 14", "unmatched reference")
    ]}
    reconciled = []
    cases = list(full_source_cases(CN, record, legacy, item(), {}, {}, citation_reconciliations=reconciled))
    assert [case["source_value"] for case in cases] == ["Café catalogue; entry 14", "unmatched reference"]
    assert all(not case["verified"] and "source_repair" not in case for case in cases)
    assert len(reconciled) == 2
    assert {row["original_occurrence"] for row in reconciled} == {"581:1:1"}
    inventory = corpus.source_corpus_inventory(
        (), reconciliations=[{**row, "control_number": CN, "qid": "Q1"} for row in reconciled],
    )
    entries = corpus.source_preflight_archive(list(inventory.values()), {CN: record}, {})
    assert {row["original_case_id"] for row in entries} == {f"source:{CN}:evidence:581:1:1"}
    assert all(row["marc"] == field and row["status"] == "source_metadata" for row in entries)

    record["marc_occurrences"].append({**field, "occurrence": 2})
    reconciled.clear()
    cases = list(full_source_cases(CN, record, legacy, item(), {}, {}, citation_reconciliations=reconciled))
    assert not reconciled
    assert len(cases) == 5


def test_current_shelfmark_uses_the_only_complete_current_holder_row():
    current = {"tag": "942", "indicators": ["1", " "], "subfields": [
        {"code": "a", "value": "Library"}, {"code": "z", "value": "Ms. 5"},
    ]}
    other = {"tag": "942", "indicators": ["4", " "], "subfields": [
        {"code": "a", "value": "Library"}, {"code": "z", "value": "Acc. 8"},
    ]}
    owner = {"tag": "710", "subfields": [
        {"code": "a", "value": "Library"}, {"code": "e", "value": "current owner"},
    ]}
    record = {"001": CN, "942$a": "Library|Library", "942$z": "Acc. 8|Ms. 5", "710$a": "Library",
              "marc_occurrences": [{"tag": "001", "data": CN}, other, current, owner]}
    entity = item()
    entity["claims"]["P217"] = [{
        "id": "Q1$shelfmark", "mainsnak": {"snaktype": "value", "datavalue": {"value": "Ms. 5"}},
        "references": [{"snaks": {"P3959": [entity["claims"]["P3959"][0]["mainsnak"]]}}],
    }]
    assert current_holding_label(CN, record, entity) == "Library, Ms. 5"
    other["indicators"] = ["1", " "]
    assert current_holding_label(CN, record, entity) is None


def test_uniform_title_occurrences_retain_named_parts_and_language():
    fields = [
        {"tag": "730", "indicators": ["0", " "], "subfields": [
            {"code": "a", "value": 'תנ"ך'}, {"code": "p", "value": "תורה"}, {"code": "l", "value": "ארמית"},
        ]},
        {"tag": "730", "indicators": ["0", "2"], "subfields": [
            {"code": "a", "value": 'תנ"ך'}, {"code": "p", "value": "ויקרא - דברים"},
        ]},
    ]
    record = prepare_record_for_pipeline({"001": CN, "marc_occurrences": fields})
    assert [row["occurrence"] for row in record["uniform_title_occurrences"]] == [1, 2]
    assert [row["subfields"] for row in record["uniform_title_occurrences"]] == [row["subfields"] for row in fields]
    assert [row["indicators"][1] for row in record["uniform_title_occurrences"]] == [" ", "2"]
    report = build_gila_audit_report(
        [{"001": CN}],
        [{"local_id": "manuscript", "records": [CN], "entity_type": "manuscript",
          "work_candidate_evidence": [{"raw_title": 'תנ""ך', "source_field": "RELATED_WORKS",
                                       "accepted": False}]}],
        people_marc={CN: record}, full_source_coverage=True,
    )
    case = next(case for case in report["records"][0]["cases"] if case.get("marc_730"))
    assert case["marc_730"] == record["uniform_title_occurrences"]
    assert case["source_field"] == "730"
    assert "source_repair" not in case


def test_reviewed_uniform_title_adds_folio_without_replacing_the_claim(monkeypatch):
    record = {"marc_occurrences": [
        {"tag": "730", "subfields": [{"code": "a", "value": "Work title"}]},
        {"tag": "500", "subfields": [{"code": "a", "value": "Work title, folios 3a-8b"}]},
    ]}
    fields = {marc.source_key(row): row for row in marc.occurrences(record)}
    manuscript, work = item(), item("Q2")
    work["labels"]["en"] = {"value": "Work title", "language": "en"}
    cited_url = nli_catalog_url(CN)
    builder = repair_claim_builder()
    occurrence = "730:1"
    original = corpus.source_patches({
        "control_number": CN, "source_repair": {
            "property": "P1574", "value": "Q2", "type": "item", "language": None,
            "qualifiers": {"P1932": "Work title"}, "proof_urls": [cited_url],
        },
    }, manuscript, builder)[0]
    original["id"] = "Q1$work"
    manuscript["claims"]["P1574"] = [original]
    proof = {
        "value": "Q2", "target_title": "Work title", "title": "Work title", "folio": "3a-8b",
        "covers": [occurrence], "urls": [cited_url],
        "extend": {"id": original["id"], "sha256": digest(semantic_entity(original))},
    }
    review = {"qid": "Q1", "uniform_fields": [occurrence],
              "fields": {key: digest(row) for key, row in fields.items()}, "works": [proof]}
    monkeypatch.setattr(uniform_titles, "uniform_title_reviews", lambda: {CN: [review]})

    def result(source, current):
        return uniform_titles.reviewed_uniform_title(CN, source, [fields[occurrence]], current, {"Q2": work})

    case = {"control_number": CN, **result(record, manuscript)}
    assert case["source_repair"]["extend_folio_claim"] == original["id"]
    patch, = corpus.source_patches(case, manuscript, builder)
    plan = PlannedEdit(manuscript, [patch], ["case"], {})
    assert corpus.source_additions_only(plan, allow_folio=True)
    assert not corpus.source_additions_only(plan)
    assert patch["id"] == original["id"]
    assert patch["qualifiers"]["P1932"] == original["qualifiers"]["P1932"]
    assert patch["references"][:1] == original["references"]
    fresh = deepcopy(manuscript)
    fresh["lastrevid"] += 1
    fresh["claims"]["P1574"] = [patch]
    assert result(record, fresh)["verified"]

    parallel = corpus.source_patches({
        "control_number": CN, "source_repair": {
            "property": "P1574", "value": "Q3", "type": "item", "language": None,
            "qualifiers": {"P1932": "Translation", "P958": "3a-8b"}, "proof_urls": [cited_url],
        },
    }, fresh, builder)[0]
    parallel["id"] = "Q1$translation"
    fresh["claims"]["P1574"].append(parallel)
    assert not uniform_titles._work_evidence(CN, fresh, work, proof)
    with_parallel = {**proof, "parallel_work_values": ["Q3"]}
    assert uniform_titles._work_evidence(CN, fresh, work, with_parallel)["verified"]
    parallel["references"] = []
    assert not uniform_titles._work_evidence(CN, fresh, work, with_parallel)

    changed = deepcopy(record)
    changed["marc_occurrences"][1]["subfields"][0]["value"] = "A different work, folios 3a-8b"
    assert not result(changed, manuscript)


def test_shelfmark_fact_uses_the_reviewed_current_number_after_the_edit(monkeypatch):
    def holding(indicator, shelfmark):
        return {"tag": "942", "indicators": [indicator, " "],
                "subfields": [{"code": "a", "value": "Library"}, {"code": "z", "value": shelfmark}]}

    old = holding("4", "Acc. 8")
    source_full = {
        "marc_occurrences": [{"tag": "001", "data": CN}, old, holding("1", "Ms. 5"), {
            "tag": "710", "subfields": [{"code": "a", "value": "Library"},
                                       {"code": "e", "value": "current owner"}],
        }],
    }
    marc.attach(source_full)
    previous, current = marc.occurrences(source_full, "942")
    proof = {
        "qid": "Q1", "property": "P217", "value": "Ms. 5", "qualifiers": {}, "type": "string",
        "source_field": marc.source_key(current), "source_sha256": digest(current),
        "previous_field": marc.source_key(previous), "previous_field_sha256": digest(previous),
    }
    monkeypatch.setattr(holdings_contents, "reviewed_corrections", lambda: {CN: proof})
    live = item()
    live["claims"]["P217"] = [{
        "id": "Q1$shelfmark", "mainsnak": {"snaktype": "value", "datavalue": {"value": "Ms. 5"}},
        "references": [{"snaks": {
            "P3959": [live["claims"]["P3959"][0]["mainsnak"]],
            "P854": [{"snaktype": "value", "datavalue": {"value": nli_catalog_url(CN)}}],
        }}],
    }]

    def audit(legacy_shelfmark):
        report = build_gila_audit_report(
            [{"001": CN, "holding_942_name": "Library", "holding_942_shelfmark": legacy_shelfmark}],
            [], qids_by_cn={CN: "Q1"}, live_entities={"Q1": live},
            people_marc={CN: source_full}, full_source_coverage=True,
        )
        return next(fact for fact in report["fact_comparison"]["facts"] if fact["property"] == "P217")

    checked = audit("Acc. 8")
    assert checked["live_status"] == "present"
    assert checked["live_provenance"] == "source_bound"
    assert checked["expected_value"] == checked["source_value"] == "Ms. 5"
    assert checked["verified_matches"] == [{"statement_id": "Q1$shelfmark", "value": "Ms. 5"}]
    assert "source_repair" not in checked
    assert audit("Unrelated number")["live_status"] != "present"


def test_reviewed_colophons_require_each_folio_before_verification():
    target = item()
    proof = {
        "property": "P1684", "value": "ונשלמה המלאכה", "qualifiers": {"P3831": "Q372474", "P7416": "ב"},
        "type": "monolingualtext", "language": "he", "proof_url": corpus.nli_catalog_url(CN),
        "additional_inscriptions": [{
            "value": "תם ונשלם", "qualifiers": {"P3831": "Q372474", "P7416": "ג"},
            "language": "he", "proof_url": corpus.nli_catalog_url(CN),
        }],
    }

    def check(entity):
        return _reviewed_source_case(CN, "957:1:1", "Two colophons", entity, proof, {})

    case = {**check(target), "control_number": CN}
    patches = corpus.source_patches(case, target, repair_claim_builder())
    assert len(patches) == 2
    assert corpus.source_additions_only(PlannedEdit(target, patches, [], {"Q1": 42}))
    fresh = deepcopy(target)
    fresh["lastrevid"] += 1
    fresh["claims"]["P1684"] = [{**patches[0], "id": "Q1$first"}]
    assert not check(fresh)["verified"]
    assert len(corpus.source_patches({**check(fresh), "control_number": CN}, fresh, repair_claim_builder())) == 1
    fresh["claims"]["P1684"].append({**patches[1], "id": "Q1$second"})
    assert verify_claims(target, fresh, fresh["lastrevid"], patches)
    assert len(check(fresh)["verified_matches"]) == 2
    fresh["claims"]["P1684"].append({**patches[0], "id": "Q1$duplicate"})
    assert "source_repair" not in check(fresh)
    assert not check(fresh)["verified"]


def test_reviewed_subject_requires_exact_work_and_catalogue_link(monkeypatch):
    manuscript, work, topic = item(), item("Q2"), item("Q3")
    control_snak = {"datavalue": {"value": CN}, "snaktype": "value"}
    manuscript["claims"]["P1574"] = [{
        "id": "Q1$work", "mainsnak": {"datavalue": {"value": {"id": "Q2"}}},
        "references": [{"snaks": {"P3959": [control_snak]}}],
    }]
    work["claims"]["P1476"] = [{"mainsnak": {"datavalue": {"value": {"text": "הלכות שחיטה"}}}}]
    proof = {
        "target_work_qid": "Q2", "target_work_title": "הלכות שחיטה",
        "supporting_note": {"occurrence": "500:1"}, "property": "P921", "value": "Q3", "type": "item",
        "qualifiers": {}, "proof_url": corpus.nli_catalog_url(CN),
    }
    monkeypatch.setattr("app.pipeline.wikidata_gila_sources.reviewed_source_proof", lambda *args: proof)
    subject = {"tag": "650", "subfields": [{"code": "a", "value": "Shehitah"}, {"code": "2", "value": "nli"}]}
    record = {"001": CN, "marc_occurrences": [subject]}

    def check():
        case, = full_source_cases(CN, record, {}, manuscript, {"Q2": work, "Q3": topic}, {})
        return case

    case = check()["source_repair"]
    assert case["qid"] == "Q2"
    assert case["dependencies"] == {"Q1": 42, "Q3": 42}
    subject["subfields"].append({"code": "z", "value": "Krako\u0301w"})
    assert "source_repair" not in check()
    proof["heading_subfields"] = deepcopy(subject["subfields"])
    proof["authority_urls"] = ["https://example.org/subject-heading"]
    case = check()
    assert case["source_repair"]["proof_text"] == "Shehitah — Kraków"
    patch = corpus.source_patches({**case, "control_number": CN}, work, repair_claim_builder())[0]
    work["claims"]["P921"] = [{**patch, "id": "Q2$subject"}]
    assert check()["verified"]
    # A matching subject without the exact heading in the reference is insufficient.
    patch["references"][0]["snaks"].pop("P1683")
    assert check()["reason"] == "source_reference_missing"
    assert check()["source_repair"]["proof_text"] == "Shehitah — Kraków"
    subject["subfields"][-1]["value"] = "Kraków"
    assert "source_repair" not in check()
    subject["subfields"].pop()
    proof.pop("heading_subfields")
    manuscript["claims"]["P1574"][0]["references"] = []
    assert check()["reason"] == "reviewed_work_scope_changed"


def test_verification_only_inscription_requires_the_existing_reference():
    target = item()
    case = inscription(target)
    proof = {**case["source_repair"], "verification_only": True, "proof_url": corpus.nli_catalog_url(CN)}
    args = CN, case["occurrence"], case["source_value"], target, proof, {}
    assert _reviewed_source_case(*args)["reason"] == "reviewed_existing_source_changed"
    patch = corpus._source_patch(case, target, repair_claim_builder())
    target["claims"]["P1684"] = [{**patch, "id": "Q1$inscription"}]
    assert _reviewed_source_case(*args)["verified"]
    target["claims"]["P1684"][0]["references"] = []
    assert "source_repair" not in _reviewed_source_case(*args)


def test_source_preflight_keeps_metadata_and_prior_cases_with_exact_marc():
    old_field = {
        "tag": "581", "occurrence": 1, "indicators": [" ", " "],
        "subfields": [{"code": "a", "value": "Catalogue, 1886"}, {"code": "3", "value": "Part two"}],
    }
    note = {
        "tag": "590", "occurrence": 1, "indicators": [" ", " "],
        "subfields": [{"code": "a", "value": "Temporary catalogue record"}],
    }
    records = {CN: {"marc_occurrences": [note]}}
    previous_records = {CN: {"marc_occurrences": [old_field]}}
    common = {"points": [8], "control_number": CN}
    current = [{
        **common, "case_id": "source:note", "occurrence": "590:1:1",
        "source_value": "Temporary catalogue record", "reason": "catalogue_status_evidence",
    }]
    previous = [{
        **common, "case_id": "source:old", "occurrence": "581:1:1", "source_value": "Catalogue, 1886",
    }]
    inventory = corpus.source_corpus_inventory(current, previous, {"source:old"})
    assert inventory["source:note"]["status"] == "source_metadata"
    assert inventory["source:old"]["status"] == "already_verified"
    archive = {row["case_id"]: row for row in corpus.source_preflight_archive(
        list(inventory.values()), records, previous_records,
    )}
    assert archive["source:note"]["marc"] == note
    assert archive["source:old"]["marc"] == old_field
    assert CN in archive["source:old"]["source_url"]


def test_reviewed_missing_scan_keeps_prior_links_and_holds_possible_duplicates(monkeypatch):
    target = item()
    url = "https://holder.example/scan/123"
    proof_url = "https://holder.example/iiif/123/manifest"
    earlier_url = "https://catalogue.example/search/123"
    proof = {
        "property": "P953", "value": url, "proof_url": proof_url, "type": "url",
        "qualifiers": {}, "prior_access_urls": [earlier_url],
    }
    monkeypatch.setattr(
        "app.pipeline.wikidata_gila_sources.reviewed_source_proof",
        lambda *args: proof,
    )
    record = {
        "001": CN, "marc_occurrences": [
            {"tag": "856", "subfields": [{"code": "u", "value": url}]},
        ],
    }

    def scan_case(entity):
        row, = full_source_cases(CN, record, {}, entity, {}, {})
        return {**row, "control_number": CN}

    def url_claim(value, claim_id):
        return {
            "id": claim_id, "rank": "normal", "mainsnak": {
                "datavalue": {"value": value}, "property": "P953", "snaktype": "value",
            },
        }

    prior = url_claim(earlier_url, "Q1$catalogue")
    target["claims"]["P953"] = [prior]
    case = scan_case(target)
    assert case["reason"] == "source_statement_missing"
    patch = corpus._source_patch(case, target, repair_claim_builder())
    assert corpus.source_additions_only(PlannedEdit(target, [patch], [], {"Q1": 42}))
    assert "id" not in patch
    after = deepcopy(target)
    after["lastrevid"] += 1
    after["claims"]["P953"].append({**patch, "id": "Q1$scan"})
    assert verify_claims(target, after, after["lastrevid"], [patch])
    assert scan_case(after)["verified"]
    target["claims"]["P953"].append(url_claim(url.replace("https:", "http:"), "Q1$equivalent"))
    assert scan_case(target)["reason"] == "access_link_requires_holding_proof"


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


@pytest.mark.asyncio
async def test_additions_only_preflight_omits_historical_removals(monkeypatch):
    target = item()

    async def load(reader, qids, items, check):
        items["Q1"] = target

    monkeypatch.setattr(corpus, "_load_items", load)
    removal_plan = AsyncMock(side_effect=AssertionError("Read only additive source proposals."))
    monkeypatch.setattr(corpus, "build_gila_repair_proposals", removal_plan)
    exact = {**inscription(target), "qid": "Q1"}
    historical = {"case_id": "historical:Q1", "qid": "Q1", "points": [8], "live_revision": 42}
    rows = [exact, historical]
    plans = await corpus.plan_sources(
        rows, {CN: source()}, check=AsyncMock(), progress=AsyncMock(),
        inventory=corpus.corpus_inventory(rows), additions_only=True,
    )
    assert [plan.case_ids for plan in plans] == [[exact["case_id"]]]
    assert corpus.source_additions_only(plans[0])
    plans[0].patches.append({"id": "Q1$source", "remove": ""})
    assert not corpus.source_additions_only(plans[0])


def test_reviewed_source_proof_requires_current_exact_marc_and_target(monkeypatch):
    from app.pipeline.wikidata_gila_reviewed_sources import reviewed_source_proof
    from hashlib import sha256

    literal = "An exact catalogue citation"
    proof = {"qid": "Q1", "source_sha256": sha256(literal.encode()).hexdigest()}
    monkeypatch.setattr(
        "app.pipeline.wikidata_gila_reviewed_sources._reviewed_sources", lambda: {(CN, "581:1:1"): proof},
    )
    assert reviewed_source_proof(CN, "581:1:1", literal, item()) == proof
    assert reviewed_source_proof(CN, "581:1:1", literal + ".", item()) is None
    assert reviewed_source_proof(CN, "581:1:1", literal, item("Q2")) is None


def test_corpus_guard_requires_all_existing_evidence():
    target = item()
    original = target["claims"]["P3959"][0]
    original["references"] = [{"snaks": {"P854": [{"url": "https://example.org/record"}]}}] * 2
    original["qualifiers"] = {"P1932": [{"text": "Catalogue spelling"}]}
    patch = {**original, "rank": "deprecated"}
    plan = PlannedEdit(target, [patch], ["case"], {"Q1": 42})
    assert corpus.preserves_claim_evidence(plan)
    assert not corpus.preserves_claim_evidence(PlannedEdit(
        target, [{**patch, "references": original["references"][:1]}], ["case"], {"Q1": 42},
    ))
    assert not corpus.preserves_claim_evidence(PlannedEdit(
        target, [{"id": original["id"], "remove": ""}], ["case"], {"Q1": 42},
    ))


def test_marc_language_repair_requires_text_language_of_the_exact_manuscript():
    from app.pipeline.wikidata_gila_facts import SourceFact, _full_marc_item_repair

    field = {"tag": "041", "ind1": "1", "ind2": " ", "subfields": [
        {"code": "a", "value": "heb"}, {"code": "h", "value": "ara"},
    ]}
    record = {"001": CN, "marc_occurrences": [field]}
    claim = SourceFact("041", "P407", "Q9288", source_value="heb")
    assert _full_marc_item_repair(CN, claim, record, item())["value"] == "Q9288"
    assert not _full_marc_item_repair(CN, SourceFact("041", "P407", "Q13955"), record, item())
    field["subfields"].append({"code": "3", "value": "preface"})
    assert not _full_marc_item_repair(CN, claim, record, item())
