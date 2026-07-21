from converter.authority.evidence import (
    AuthorityEvidence,
    evidence_from_values,
    gate_candidates,
    normalize_viaf_id,
    normalize_wikidata_qid,
)


def test_normalize_external_identifiers() -> None:
    assert normalize_wikidata_qid("https://www.wikidata.org/entity/q1218") == "Q1218"
    assert normalize_viaf_id("https://viaf.org/viaf/12345678") == "12345678"
    assert normalize_wikidata_qid("Q0") is None
    assert normalize_viaf_id("not-an-id") is None


def test_conflicting_qids_abstain_but_remain_evidence() -> None:
    result = gate_candidates(
        [
            AuthorityEvidence("kima", "Q1218", "wikidata", True),
            AuthorityEvidence("sameAs", "Q1370", "wikidata", True),
        ]
    )
    assert len(result) == 2
    assert all(not item.accepted for item in result)
    assert all(item.reason == "conflicting candidates" for item in result)


def test_matching_duplicate_forms_are_accepted_once() -> None:
    result = evidence_from_values(
        [
            ("external_wikidata_uri", "https://www.wikidata.org/entity/Q1218"),
            ("sameAs", "Q1218"),
            ("viaf_id", "12345678"),
        ]
    )
    assert [(item.kind, item.identifier, item.accepted) for item in result] == [
        ("viaf", "12345678", True),
        ("wikidata", "Q1218", True),
    ]
