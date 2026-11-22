"""Exercise the NLI import, exact role correspondence, and the scoped release."""

from types import SimpleNamespace

import pytest

from app.pipeline import wikidata_gila_audit_job as audit_job
from app.pipeline.wikidata_gila_people import parse_nli_records, people_role_cases
from app.pipeline.wikidata_publication_prepare_job import audited_batch_params

CONTROL = "990000403660205171"


def _response(*records):
    xml = (
        '<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">'
        '<records>' + "".join(f"<record><recordData>{record}</recordData></record>" for record in records)
        + "</records></searchRetrieveResponse>"
    )
    return xml.encode()


def _record(fields, cn=CONTROL):
    return (
        '<record xmlns="http://www.loc.gov/MARC21/slim">'
        f'<controlfield tag="001">{cn}</controlfield>{fields}</record>'
    )


def _field(tag, *subfields):
    values = "".join(f'<subfield code="{code}">{value}</subfield>' for code, value in subfields)
    return f'<datafield tag="{tag}" ind1=" " ind2=" ">{values}</datafield>'


def _snak(value):
    return {"snaktype": "value", "datavalue": {"value": value}}


def _claim(value, *, refs=True, qualifiers=None):
    row = {
        "mainsnak": _snak(value),
        "qualifiers": {pid: [_snak(value)] for pid, value in (qualifiers or {}).items()},
    }
    if refs:
        row["references"] = [{"snaks": {"P3959": [_snak(CONTROL)]}}]
    return row


def test_nli_import_preserves_roles_ids_and_local_fields():
    marc = _field("AVA", ("a", "local")) + _field("700", ("a", "Name"), ("e", "scribe"), ("0", "(VIAF)123"))
    batch = parse_nli_records(_response(_record(marc)), {CONTROL})
    row = batch[CONTROL]
    assert row["marc_occurrences"][-1]["subfields"][2] == {"code": "0", "value": "(VIAF)123"}
    assert 'tag="AVA"' in row["nli_local_fields"][0]


@pytest.mark.parametrize("controls", [{"990000403370205171"}, {CONTROL}])
def test_nli_import_rejects_an_unexpected_or_duplicate_record(controls):
    with pytest.raises(ValueError, match="duplicate or unexpected"):
        parse_nli_records(_response(_record(""), _record("")), controls)


def test_one_role_check_joins_the_script_variant_and_checks_exact_authority():
    fields = (
        _field("700", ("a", "Source Person"), ("e", "scribe"), ("6", "880-01"), ("0", "(VIAF)123"))
        + _field("880", ("a", "Alternate Person"), ("6", "700-01"))
        + _field("700", ("a", "Source Person"), ("e", "former owner"))
    )
    record = parse_nli_records(_response(_record(fields)), {CONTROL})[CONTROL]
    person = {"id": "Q42", "labels": {"en": {"value": "Alternate Person"}},
              "claims": {"P31": [_claim({"id": "Q5"})], "P214": [_claim("123")]}}
    manuscript = {"claims": {
        "P3959": [_claim(CONTROL)],
        "P11603": [_claim({"id": "Q42"})],
        "P3342": [_claim({"id": "Q42"}, qualifiers={"P3831": {"id": "Q10359183"}})],
    }}
    placeholder = _claim(None, qualifiers={"P1932": "Source Person"})
    placeholder["mainsnak"] = {"snaktype": "somevalue"}
    manuscript["claims"]["P11603"].insert(0, placeholder)
    cases = people_role_cases(record, manuscript, {"Q42": person})
    assert [(row["person_role"], row["verified"]) for row in cases] == [("scribe", True), ("former owner", False)]
    assert cases[1]["reason"] == "person_role_needs_evidence"
    person["claims"]["P214"] = [_claim("999")]
    assert not people_role_cases(record, manuscript, {"Q42": person})[0]["verified"]


def test_selected_people_release_cannot_include_unselected_or_non_people_edits(monkeypatch):
    rows = [
        {"case_id": "scribe", "local_id": "one", "qid": "Q10", "candidate_qid": "Q99", "points": [5]},
        {"case_id": "outside", "local_id": "other", "qid": "Q20", "points": [5]},
        {"case_id": "work", "local_id": "one", "qid": "Q10", "points": [3], "can_prepare": True},
    ]
    monkeypatch.setattr(audit_job, "gila_cases", lambda _: rows)
    report = SimpleNamespace(id="audit", params={"_gila_build_revision": 1, "_gila_build_digest": "d",
                                                 "_gila_source_fingerprint": "f"})
    evidence = SimpleNamespace(id="evidence", status="succeeded", result={"_repairs": rows})
    assert audited_batch_params(report, "actor", evidence=evidence, people_links={"Q10": "Q99"})["_gila_batch"] == [
        ("one", "Q10", False),
    ]

    assert audited_batch_params(report, "actor", evidence=evidence, people_links={"Q10": "Q98"}) is None
