from copy import deepcopy

import pytest

from app.pipeline import marc_occurrences
from app.pipeline.wikidata_source_choices import (
    apply_choices,
    candidates,
    make_choice,
    source_hash,
    validate_choices,
)
from app.pipeline.wikidata_source_coverage import source_coverage
from converter.wikidata.item_models import WikidataItem


def work_record():
    return {
        "001": "990001",
        "title": "אסופה",
        "contents": [
            {
                "title": "עץ חיים",
                "author": "חיים ויטאל",
                "folio_range": "21ב–22ב",
                "source_field": "505",
            },
            {
                "title": "עץ חיים",
                "author": "חיים ויטאל",
                "folio_range": "31ב–32ב",
                "source_field": "505",
            },
        ],
    }


def claim(value):
    return {"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"value": value}}}


def work_target():
    return {
        "id": "Q123",
        "type": "item",
        "lastrevid": 42,
        "labels": {"he": {"value": "עץ חיים"}},
        "claims": {"P31": [claim({"id": "Q47461344"})], "P2093": [claim("חיים ויטאל")]},
    }


def choice(record, candidate, target=None):
    return make_choice(
        record,
        candidate["candidate_key"],
        source_hash(record),
        target=target,
        evidence_uri="https://www.nli.org.il/test",
        reason="The source title and responsibility match the target.",
    )


def test_choice_preserves_distinct_folios_and_is_idempotent():
    raw = work_record()
    entries = [c for c in candidates(raw) if c["title"] == "עץ חיים"]
    proofs = [choice(raw, c, work_target()) for c in entries]
    item = WikidataItem(local_id="ms:1", entity_type="manuscript", records=["990001"])
    apply_choices(item, proofs, {"990001": raw})
    apply_choices(item, proofs, {"990001": raw})
    links = [s for s in item.statements if s.property_id == "P1574"]
    assert len(links) == 2
    assert {q["value"] for s in links for q in s.qualifiers if q["property"] == "P958"} == {
        "21ב–22ב",
        "31ב–32ב",
    }
    assert all(s.reference_blocks for s in links)


def test_source_candidates_work_in_a_fresh_interpreter():
    import subprocess
    import sys

    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            "from app.pipeline.wikidata_source_choices import candidates; "
            "assert isinstance(candidates({'001': '990001'}), list)",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("change", ["author", "title", "folio_range"])
def test_source_change_invalidates_the_choice(change):
    raw = work_record()
    proof = choice(raw, candidates(raw)[0], work_target())
    newer = deepcopy(raw)
    newer["contents"][0][change] = "changed"
    with pytest.raises(ValueError, match="stale"):
        validate_choices([proof], {"990001": newer})


@pytest.mark.parametrize("defect", ["author", "title", "scope", "redirect", "missing"])
def test_homonymous_wrong_scope_and_incomplete_targets_fail(defect):
    raw = work_record()
    target = work_target()
    if defect == "author":
        target["claims"]["P2093"] = [claim("Other author")]
    elif defect == "title":
        target["labels"]["he"]["value"] = "Other work"
    elif defect == "scope":
        target["claims"]["P31"] = [claim({"id": "Q5"})]
    elif defect == "redirect":
        target["redirects"] = {"from": "Q123", "to": "Q124"}
    else:
        target.pop("lastrevid")
    with pytest.raises(ValueError):
        choice(raw, candidates(raw)[0], target)


def test_a_title_alone_never_establishes_a_work_identity():
    raw = work_record()
    raw["contents"][0].pop("author")
    with pytest.raises(ValueError, match="title alone"):
        choice(raw, candidates(raw)[0], work_target())


def test_bibliography_retains_original_text_and_validates_year_and_pages():
    raw = {"001": "990001", "581$a": "Weisz. Catalogue of manuscripts. 1906, pp. 12–14"}
    entry = next(c for c in candidates(raw) if c["kind"] == "catalog_reference")
    assert entry["year"] == 1906 and entry["pages"] == "12–14"
    target = {
        "id": "Q123",
        "type": "item",
        "lastrevid": 10,
        "labels": {"en": {"value": "Catalogue of manuscripts"}},
        "claims": {"P577": [claim({"time": "+1906-01-01T00:00:00Z"})]},
    }
    proof = choice(raw, entry, target)
    item = WikidataItem(local_id="ms:1", entity_type="manuscript", records=["990001"])
    apply_choices(item, [proof], {"990001": raw})
    assert [(s.property_id, s.value) for s in item.statements] == [("P1343", "Q123")]
    assert item.statements[0].qualifiers == [
        {"property": "P304", "value": "12–14", "type": "string"}
    ]
    target["claims"]["P577"] = [claim({"time": "+1907-01-01T00:00:00Z"})]
    with pytest.raises(ValueError, match="year"):
        choice(raw, entry, target)


def test_source_hash_is_stable_across_marc_json_normalization():
    raw = {
        "fields": [
            {"001": "990001"},
            {"942": {"ind1": " ", "ind2": " ", "subfields": [{"a": "Library"}, {"z": "Ms 1"}]}},
        ]
    }
    normalized = deepcopy(raw)
    marc_occurrences.attach(normalized)
    assert source_hash(raw) == source_hash(normalized)
    proof = choice(raw, candidates(raw)[0])
    assert validate_choices([proof], {"990001": normalized}) == [proof]


def test_catalogue_status_never_counts_as_an_inscription_or_verified_passage():
    raw = {"001": "990001", "590$a": "רשומה זמנית", "500$a": "Unparsed source passage"}
    result = source_coverage(raw)
    assert result["record_status_notes"] == ["רשומה זמנית"]
    assert {row["source_field"] for row in result["passages"]} >= {"500", "590"}
    assert not result["all_passages_verified"]
    assert result["model_decision"]["status"] == "documented_model_difference"


def test_rejected_work_match_stays_unresolved_without_a_new_work_item():
    from converter.wikidata.item_models import WikidataStatement

    raw = work_record()
    candidate = candidates(raw)[0]
    proof = make_choice(
        raw,
        candidate["candidate_key"],
        source_hash(raw),
        target=work_target(),
        decision="reject",
        reason="The folio attests a different work.",
        evidence_uri="https://www.nli.org.il/test",
    )
    item = WikidataItem(
        local_id="ms:1",
        entity_type="manuscript",
        records=["990001"],
        statements=[
            WikidataStatement(
                "P1574",
                "Q123",
                "item",
                qualifiers=[
                    {"property": "P1932", "value": "עץ חיים", "type": "string"},
                    {"property": "P958", "value": "21ב–22ב", "type": "string"},
                ],
            )
        ],
    )
    apply_choices(item, [proof], {"990001": raw})
    assert item.statements[0].value == "Q234460"
    assert item.authority_evidence[0]["accepted"] is False


def test_documented_passage_remains_a_review_case_without_public_claims():
    raw = {"001": "990001", "500$a": "Unparsed source passage"}
    candidate = next(
        row for row in candidates(raw) if row["kind"] == "passage" and row["source_field"] == "500"
    )
    proof = make_choice(
        raw,
        candidate["candidate_key"],
        source_hash(raw),
        decision="document",
        reason="The passage needs a material-unit scope review.",
        evidence_uri="https://www.nli.org.il/test",
    )
    result = source_coverage(raw, [proof])
    passage = next(row for row in result["passages"] if row["source_field"] == "500")
    assert passage["status"] == "documented_unresolved"
    assert not result["all_passages_verified"]
    item = WikidataItem(local_id="ms:1", entity_type="manuscript", records=["990001"])
    apply_choices(item, [proof], {"990001": raw})
    assert not item.statements


def test_ambiguous_holdings_and_nonnumeric_extent_are_not_source_absence():
    result = source_coverage(
        {"001": "990001", "942$a": "First|Second", "942$z": "Ms 1|Ms 2", "300$a": "כרך ללא מספור"}
    )
    assert not result["source_gaps"]
    assert result["requirements"][0]["status"] == "source_review"


def test_current_targets_remain_specific_to_the_title_and_folio():
    raw = work_record()
    item = {
        "statements": [
            {
                "property": "P1574",
                "value": "Q123",
                "qualifiers": [
                    {"property": "P1932", "value": "עץ חיים"},
                    {"property": "P958", "value": "21ב–22ב"},
                ],
            },
            {
                "property": "P1574",
                "value": "Q124",
                "qualifiers": [
                    {"property": "P1932", "value": "עץ חיים"},
                    {"property": "P958", "value": "31ב–32ב"},
                ],
            },
        ]
    }
    result = source_coverage(raw, item=item)
    works = [row for row in result["candidates"] if row["title"] == "עץ חיים"]
    assert [(row["folio"], row["current_targets"]) for row in works] == [
        ("21ב–22ב", ["Q123"]),
        ("31ב–32ב", ["Q124"]),
    ]


def test_a_main_entry_scribe_cannot_become_the_work_author():
    raw = {"001": "990001", "245$a": "סוכה", "100$a": "רפאל מקרויא", "100$e": "scribe"}
    main = next(row for row in candidates(raw) if row["kind"] == "work_occurrence")
    assert main["author"] == ""


def test_ambiguous_enhanced_scopes_require_review_before_a_work_choice():
    raw = {
        "fields": [
            {"001": "990001"},
            {
                "505": {
                    "subfields": [
                        {"g": "21ב–22ב"},
                        {"t": "עץ חיים"},
                        {"r": "חיים ויטאל"},
                        {"g": "31ב–32ב"},
                        {"t": "ספר הזוהר"},
                    ]
                }
            },
        ]
    }
    candidate = next(row for row in candidates(raw) if row["title"] == "עץ חיים")
    assert candidate["scope_alignment_unproven"] is True
    with pytest.raises(ValueError, match="source-scope"):
        choice(raw, candidate, work_target())


@pytest.mark.parametrize("kind", ["catalog_reference", "work_occurrence"])
def test_the_audit_requires_current_target_identity_and_a_public_source_reference(kind):
    from app.pipeline.wikidata_gila_facts import compare_gila_source_facts
    from app.pipeline.wikidata_studio import native_item_payload

    if kind == "catalog_reference":
        raw = {"001": "990001", "581$a": "Catalogue of manuscripts 1906, pp. 12–14"}
        target = {
            "id": "Q123",
            "type": "item",
            "lastrevid": 10,
            "labels": {"en": {"value": "Catalogue of manuscripts"}},
            "claims": {"P577": [claim({"time": "+1906-01-01T00:00:00Z"})]},
        }
        pid = "P1343"
    else:
        raw, target, pid = work_record(), work_target(), "P1574"
    candidate = next(row for row in candidates(raw) if row["kind"] == kind)
    proof = choice(raw, candidate, target)
    item = WikidataItem(local_id="ms:1", entity_type="manuscript", records=["990001"])
    apply_choices(item, [proof], {"990001": raw})
    native = native_item_payload(item)
    statement = next(
        row
        for row in native["statements"]
        if (row.get("property") or row.get("property_id")) == pid
    )

    def snak(property_id, value):
        return {
            "property": property_id,
            "snaktype": "value",
            "datavalue": {"value": {"id": value} if str(value).startswith("Q") else value},
        }

    public_claim = {
        "rank": "normal",
        "mainsnak": snak(pid, "Q123"),
        "qualifiers": {
            q["property"]: [snak(q["property"], q["value"])] for q in statement["qualifiers"]
        },
        "references": [{"snaks": {"P3959": [snak("P3959", "990001")]}}],
    }
    live = {"Q10": {"id": "Q10", "lastrevid": 50, "claims": {pid: [public_claim]}}, "Q123": target}

    def fact(snapshot):
        report = compare_gila_source_facts(
            [raw], [native], qids_by_cn={"990001": "Q10"}, live_entities=snapshot
        )
        return next(
            row
            for row in report["facts"]
            if row["property"] == pid and row["expected_value"] == "Q123"
        )

    assert fact(live)["live_status"] == "present"
    assert fact(live)["live_provenance"] == "source_bound"
    without_target = {"Q10": live["Q10"]}
    assert fact(without_target)["live_status"] != "present"
    public_claim["references"] = []
    assert fact(live)["live_provenance"] != "source_bound"


def test_conflicting_current_library_choices_fail_closed():
    raw = {"001": "990001", "942$a": "Library A|Library B", "942$z": "Ms 1|Ms 2"}
    proofs = [choice(raw, row) for row in candidates(raw) if row["kind"] == "holding"]
    with pytest.raises(ValueError, match="conflicting current-library"):
        validate_choices(proofs, {"990001": raw})


def test_an_unproven_city_cannot_become_an_instruction_to_replace_a_public_label():
    from app.pipeline.wikidata_gila_audit import build_gila_audit_report

    raw = {
        "001": "990001",
        "942$a": "Current Library|Former Library",
        "942$z": "Ms 1|Ms 2",
        "942$x": "City",
        "710$a": "Current Library|Former Library",
        "710$e": "current owner|former owner",
    }
    item = {
        "local_id": "ms:1",
        "entity_type": "manuscript",
        "records": ["990001"],
        "labels": {"en": "Current Library. City. Ms 1"},
        "statements": [],
    }
    report = build_gila_audit_report([raw], [item])
    cases = report["records"][0]["cases"]
    assert any(
        case["reason"] == "holding_associations_require_original_occurrences" for case in cases
    )
    assert not any(case["reason"] == "structured_english_label_differs" for case in cases)


@pytest.mark.parametrize("decision", ["select", "reject"])
@pytest.mark.parametrize("scope", ["shared", "other", "unproven", "aliased_shared"])
def test_a_work_choice_cannot_retarget_another_records_shared_claim(decision, scope):
    from converter.wikidata.item_models import WikidataStatement
    from converter.wikidata.property_mapping import nli_reference

    raw = work_record()
    other = {**deepcopy(raw), "001": "990002"}
    candidate = candidates(raw)[0]
    proof = make_choice(
        raw,
        candidate["candidate_key"],
        source_hash(raw),
        target=work_target(),
        decision=decision,
        evidence_uri="https://www.nli.org.il/test",
        reason="Review this exact record.",
    )
    blocks = [] if scope == "unproven" else [nli_reference("990002")]
    if scope in {"shared", "aliased_shared"}:
        blocks.append(nli_reference("990001"))
    if scope == "aliased_shared":
        blocks = [
            [
                {"property_id" if key == "property" else key: value for key, value in snak.items()}
                for snak in block
            ]
            for block in blocks
        ]
    statement = WikidataStatement(
        "P1574",
        "Q999" if decision == "select" else "Q123",
        "item",
        qualifiers=[
            {"property": "P1932", "value": "עץ חיים", "type": "string"},
            {"property": "P958", "value": "21ב–22ב", "type": "string"},
        ],
        references=[snak for block in blocks for snak in block],
        reference_blocks=blocks,
    )
    item = WikidataItem(
        local_id="ms:1",
        entity_type="manuscript",
        records=["990001", "990002"],
        statements=[statement],
    )
    before = deepcopy(statement)
    with pytest.raises(ValueError, match="source|record|scope"):
        apply_choices(item, [proof], {"990001": raw, "990002": other})
    assert item.statements == [before]
    assert not item.authority_evidence


def test_a_rejected_citation_preserves_other_page_ranges():
    from converter.wikidata.item_models import WikidataStatement
    from converter.wikidata.property_mapping import nli_reference

    raw = {"001": "990001", "581$a": "Catalogue of manuscripts 1906, pp. 12–14"}
    candidate = next(c for c in candidates(raw) if c["kind"] == "catalog_reference")
    proof = make_choice(
        raw,
        candidate["candidate_key"],
        source_hash(raw),
        target=work_target(),
        decision="reject",
        evidence_uri="https://www.nli.org.il/test",
        reason="This citation names another publication.",
    )
    statements = [
        WikidataStatement(
            "P1343",
            "Q123",
            "item",
            qualifiers=[{"property": "P304", "value": pages, "type": "string"}],
            references=nli_reference("990001"),
        )
        for pages in ["12–14", "15–17"]
    ]
    item = WikidataItem(
        local_id="ms:1",
        entity_type="manuscript",
        records=["990001"],
        statements=deepcopy(statements),
    )
    apply_choices(item, [proof], {"990001": raw})
    assert item.statements == [statements[1]]


def test_a_selected_citation_retains_the_exact_record_reference():
    from converter.wikidata.item_models import WikidataStatement
    from converter.wikidata.property_mapping import nli_reference

    raw = {"001": "990001", "581$a": "Catalogue of manuscripts 1906, pp. 12–14"}
    candidate = next(c for c in candidates(raw) if c["kind"] == "catalog_reference")
    target = {
        "id": "Q123",
        "type": "item",
        "lastrevid": 10,
        "labels": {"en": {"value": "Catalogue of manuscripts"}},
        "claims": {"P577": [claim({"time": "+1906-01-01T00:00:00Z"})]},
    }
    proof = choice(raw, candidate, target)
    item = WikidataItem(
        local_id="ms:1",
        entity_type="manuscript",
        records=["990001"],
        statements=[
            WikidataStatement(
                "P1343",
                "Q123",
                "item",
                qualifiers=[{"property": "P304", "value": "12–14", "type": "string"}],
                references=nli_reference("990002"),
            )
        ],
    )
    apply_choices(item, [proof], {"990001": raw})
    assert len(item.statements) == 1
    assert {
        snak["value"]
        for block in item.statements[0].reference_blocks
        for snak in block
        if snak["property"] == "P3959"
    } == {"990001", "990002"}


@pytest.mark.parametrize("scope", ["shared", "unproven", "other"])
def test_a_rejected_citation_cannot_remove_another_records_evidence(scope):
    from converter.wikidata.item_models import WikidataStatement
    from converter.wikidata.property_mapping import nli_reference

    raw = {"001": "990001", "581$a": "Catalogue of manuscripts 1906, pp. 12–14"}
    candidate = next(c for c in candidates(raw) if c["kind"] == "catalog_reference")
    proof = make_choice(
        raw,
        candidate["candidate_key"],
        source_hash(raw),
        target=work_target(),
        decision="reject",
        evidence_uri="https://www.nli.org.il/test",
        reason="Review only this source citation.",
    )
    blocks = [] if scope == "unproven" else [nli_reference("990002")]
    if scope == "shared":
        blocks.append(nli_reference("990001"))
    statement = WikidataStatement(
        "P1343",
        "Q123",
        "item",
        qualifiers=[{"property": "P304", "value": "12–14", "type": "string"}],
        references=[snak for block in blocks for snak in block],
        reference_blocks=blocks,
    )
    item = WikidataItem(
        local_id="ms:1",
        entity_type="manuscript",
        records=["990001", "990002"],
        statements=[statement],
    )
    before = deepcopy(statement)
    if scope == "other":
        apply_choices(item, [proof], {"990001": raw})
    else:
        with pytest.raises(ValueError, match="source-record scope"):
            apply_choices(item, [proof], {"990001": raw})
    assert item.statements == [before]


def test_a_library_choice_cannot_replace_another_records_shelfmark():
    from converter.wikidata.item_models import WikidataStatement
    from converter.wikidata.property_mapping import nli_reference

    raw = {"001": "990001", "942$a": "Current Library", "942$z": "Ms 1"}
    candidate = next(c for c in candidates(raw) if c["kind"] == "holding")
    proof = choice(raw, candidate)
    statement = WikidataStatement("P217", "Ms 2", "string", references=nli_reference("990002"))
    item = WikidataItem(
        local_id="ms:1",
        entity_type="manuscript",
        records=["990001", "990002"],
        statements=[statement],
    )
    before = deepcopy(statement)
    with pytest.raises(ValueError, match="source-record scope"):
        apply_choices(item, [proof], {"990001": raw})
    assert item.statements == [before]


def test_a_library_choice_preserves_sources_on_an_unchanged_shelfmark():
    from converter.wikidata.item_models import WikidataStatement
    from converter.wikidata.property_mapping import nli_reference

    raw = {"001": "990001", "942$a": "Current Library", "942$z": "Ms 1"}
    candidate = next(c for c in candidates(raw) if c["kind"] == "holding")
    statement = WikidataStatement(
        "P217", "Ms 1", "string", references=nli_reference("990002"), rank="preferred"
    )
    item = WikidataItem(
        local_id="ms:1",
        entity_type="manuscript",
        records=["990001", "990002"],
        statements=[statement],
    )
    apply_choices(item, [choice(raw, candidate)], {"990001": raw})
    retained = next(s for s in item.statements if s.property_id == "P217")
    assert retained.rank == "preferred"
    assert {
        s["value"] for block in retained.reference_blocks for s in block if s["property"] == "P3959"
    } == {"990001", "990002"}


def test_work_choices_with_the_same_public_scope_cannot_overwrite_each_other():
    raw = work_record()
    raw["contents"][1].update(folio_range="21ב–22ב", author="Another author")
    entries = [
        c for c in candidates(raw) if c["kind"] == "work_occurrence" and c["title"] == "עץ חיים"
    ]
    targets = [work_target(), work_target()]
    targets[1].update(id="Q124")
    targets[1]["claims"]["P2093"] = [claim("Another author")]
    proofs = [
        choice(raw, candidate, target) for candidate, target in zip(entries, targets, strict=True)
    ]
    item = WikidataItem(local_id="ms:1", entity_type="manuscript", records=["990001"])
    with pytest.raises(ValueError, match="conflicting.*scope"):
        apply_choices(item, proofs, {"990001": raw})
    assert not item.statements


def test_a_library_choice_preserves_historical_shelfmarks():
    from converter.wikidata.item_models import WikidataStatement
    from converter.wikidata.property_mapping import nli_reference

    raw = {"001": "990001", "942$a": "Current Library", "942$z": "Ms 1"}
    candidate = next(c for c in candidates(raw) if c["kind"] == "holding")
    historical = WikidataStatement(
        "P217",
        "Old shelfmark",
        "string",
        references=nli_reference("990002"),
        qualifiers=[
            {"property": "P582", "value": "+1900-01-01T00:00:00Z", "type": "time", "precision": 9}
        ],
    )
    item = WikidataItem(
        local_id="ms:1",
        entity_type="manuscript",
        records=["990001", "990002"],
        statements=[deepcopy(historical)],
    )
    apply_choices(item, [choice(raw, candidate)], {"990001": raw})
    assert historical in item.statements
    assert any(s.property_id == "P217" and s.value == "Ms 1" for s in item.statements)
