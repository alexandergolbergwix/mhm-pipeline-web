from app.pipeline.hmo_wikidata_projection import canonical_hmo_instance_qids


def test_accepts_only_exact_canonical_uri_and_qid() -> None:
    expected = {"990001": "https://w3id.org/mhm/ontology#MS_990001"}
    assert canonical_hmo_instance_qids(
        [(expected["990001"], "Q1218"), ("https://example.invalid/MS_990001", "Q2")],
        expected,
    ) == {"990001": "Q1218"}


def test_rejects_conflicting_qids_for_one_control_number() -> None:
    uri = "https://w3id.org/mhm/ontology#MS_990001"
    assert canonical_hmo_instance_qids([(uri, "Q1218"), (uri, "Q1389")], {"990001": uri}) == {}


def test_rejects_malformed_qids() -> None:
    uri = "https://w3id.org/mhm/ontology#MS_990001"
    assert canonical_hmo_instance_qids([(uri, "Q0"), (uri, "QDraft_990001")], {"990001": uri}) == {}
