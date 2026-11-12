"""Author identity and deferred source notes in the live source audit."""

from copy import deepcopy

import pytest

from app.pipeline.wikidata_gila_facts import compare_gila_source_facts


def _live_claim(value: object, qualifiers: dict[str, str] | None = None) -> dict[str, object]:
    return {
        "mainsnak": {"snaktype": "value", "datavalue": {"value": value}},
        "qualifiers": {
            pid: [{"snaktype": "value", "datavalue": {"value": text}}]
            for pid, text in (qualifiers or {}).items()
        },
    }


def _author_case(source_author: str = "מחבר אחד") -> tuple[list, list, dict]:
    record = {"001": "990001", "contents": [{
        "title": "ספר הדרושים", "author": source_author, "source_field": "505",
    }]}
    items = [{
        "entity_type": "manuscript", "local_id": "ms", "records": ["990001"],
        "statements": [{
            "property": "P1574", "value": "Q200",
            "qualifiers": [{"property": "P1932", "value": "ספר הדרושים"}],
        }],
    }, {
        "entity_type": "work", "local_id": "work", "existing_qid": "Q200",
        "records": ["990001"], "statements": [{
            "property": "P50", "value": "Q100",
            "qualifiers": [{"property": "P1932", "value": source_author}],
        }],
    }]
    live = {
        "Q10": {"id": "Q10", "claims": {"P1574": [
            _live_claim({"id": "Q200"}, {"P1932": "ספר הדרושים"}),
        ]}},
        "Q200": {"id": "Q200", "claims": {"P50": [_live_claim({"id": "Q100"})]}},
    }
    return [record], items, live


def _work_fact(records: list, items: list, live: dict, approved: dict | None = None) -> dict:
    result = compare_gila_source_facts(
        records, items, qids_by_cn={"990001": "Q10"}, live_entities=live,
        approved_author_qids=(
            {("990001", "מחבר אחד"): ["Q100"]} if approved is None else approved
        ),
    )
    return next(f for f in result["facts"] if f["property"] == "P1574")


def test_source_qualified_native_p50_matches_the_exact_public_author() -> None:
    records, items, live = _author_case()
    fact = _work_fact(records, items, live)
    assert fact["proposal_status"] == fact["live_status"] == "present"
    assert fact["review_status"] == "pending"


@pytest.mark.parametrize("conflict", ["different_author", "deprecated_native", "ambiguous_native"])
def test_p50_agreement_does_not_hide_an_identity_conflict(conflict: str) -> None:
    records, items, live = _author_case()
    if conflict == "different_author":
        live["Q200"]["claims"]["P50"] = [_live_claim({"id": "Q101"})]
    elif conflict == "deprecated_native":
        items[1]["statements"][0]["rank"] = "deprecated"
    else:
        items.append(deepcopy(items[1]))
    assert _work_fact(records, items, live, approved={})["live_status"] == "identity_review"


def test_native_author_id_without_approved_source_evidence_stays_in_review() -> None:
    records, items, live = _author_case()
    assert _work_fact(records, items, live, approved={})["live_status"] == "identity_review"


def test_author_approval_does_not_cross_source_records() -> None:
    records, items, live = _author_case()
    assert _work_fact(
        records, items, live, approved={("990002", "מחבר אחד"): ["Q100"]},
    )["live_status"] == "identity_review"


def test_literal_author_quote_normalization_preserves_the_uncertainty_mark() -> None:
    records, items, live = _author_case('דוד בן אברהם הרמב""ם(?)')
    items[1]["statements"] = [{"property": "P2093", "value": 'דוד בן אברהם הרמב"ם(?)'}]
    live["Q200"]["claims"] = {"P2093": [_live_claim('דוד בן אברהם הרמב"ם(?)')]}
    assert _work_fact(records, items, live)["live_status"] == "present"
    live["Q200"]["claims"] = {"P2093": [_live_claim('דוד בן אברהם הרמב"ם')]}
    assert _work_fact(records, items, live)["live_status"] == "identity_review"


@pytest.mark.parametrize(("title", "reason", "expected"), [
    ("בין השאר מדרש איכה בשולי המגלה", "catalogue_prose", "source_evidence"),
    ("ספר הבריאה (חלק ב)", "bibliographic_fragment", "identity_review"),
])
def test_deferred_notes_and_structured_parts_have_distinct_review_states(
    title: str, reason: str, expected: str,
) -> None:
    records = [{"001": "990001", "contents": [{"title": title, "source_field": "505"}]}]
    items = [{
        "entity_type": "manuscript", "records": ["990001"], "statements": [],
        "work_candidate_evidence": [{
            "raw_title": title, "folio_range": "", "accepted": False, "reason": reason,
        }],
    }]
    fact = _work_fact(records, items, {"Q10": {"id": "Q10", "claims": {}}})
    assert fact["proposal_status"] == "deferred"
    assert fact["live_status"] == expected
