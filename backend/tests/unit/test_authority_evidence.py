from converter.authority.evidence import (
    AuthorityEvidence,
    evidence_from_values,
    evidence_row_ids,
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


def test_explicit_kima_and_mazal_ids_are_supported() -> None:
    result = evidence_from_values(
        [
            ("kima_id", "42"),
            ("external_uri_nli", "https://www.nli.org.il/en/authorities/987007414776605171"),
        ]
    )
    assert [(item.kind, item.identifier, item.accepted) for item in result] == [
        ("kima", "42", True),
        ("mazal", "987007414776605171", True),
    ]


def test_numeric_literals_without_authority_predicate_are_ignored() -> None:
    assert evidence_from_values(
        [("latitude", "42"), ("date", "987007414776605171"), ("external_uri_nli", "123456")]
    ) == []


def test_evidence_row_ids_reads_hmo_and_legacy_shapes() -> None:
    assert evidence_row_ids({
        "kind": "viaf",
        "identifier": "123456789",
        "accepted": True,
    }) == {"viaf": "123456789"}
    assert evidence_row_ids({
        "viaf_uri": "https://viaf.org/viaf/123456789",
        "mazal_id": "987000000000000004",
        "wikidata_qid": "Q1218",
    }) == {
        "viaf": "https://viaf.org/viaf/123456789",
        "mazal": "987000000000000004",
        "wikidata": "Q1218",
    }
    assert evidence_row_ids({"kind": "viaf", "viaf_id": "123456789"}) == {"viaf": "123456789"}
