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


@pytest.mark.asyncio
@pytest.mark.parametrize(("retry_after", "viaf_requests"), [("2", 2), ("120", 1)])
async def test_audit_keeps_scribe_verification_across_viaf_rate_limit(monkeypatch, retry_after, viaf_requests):
    import httpx

    from app.pipeline import wikidata_gila_authority_reads as reads

    record = parse_nli_records(_response(_record(_field(
        "700", ("a", "אבן צור, יעקב בן ראובן"), ("d", "1673-1752"), ("e", "scribe"),
    ))), {CONTROL})[CONTROL]
    candidate = {
        "id": "Q42", "claims": {"P31": [_claim({"id": "Q5"})],
        "P214": [_claim("248556677")], "P244": [_claim("n85362651")]},
    }
    linked = _claim({"id": "Q42"}, qualifiers={"P1932": "אבן צור, יעקב בן ראובן"})
    linked["references"].append({"snaks": {"P854": [_snak("https://viaf.org/viaf/248556677")]}})
    manuscript = {"claims": {"P3959": [_claim(CONTROL)], "P11603": [linked]}}
    entities = {"Q10": manuscript, "Q42": candidate}
    authority = [{
        "@id": "http://id.loc.gov/authorities/names/n85362651",
        "@type": ["http://www.loc.gov/mads/rdf/v1#PersonalName", "http://www.loc.gov/mads/rdf/v1#Authority"],
        "http://www.loc.gov/mads/rdf/v1#authoritativeLabel": [{"@value": "אבן צור, יעקב בן ראובן, 1673־1752"}],
    }]
    requests = []
    delays = []

    async def no_wait(seconds):
        delays.append(seconds)

    def serve(request):
        requests.append(request.url.host)
        if request.url.host == "viaf.org":
            return httpx.Response(429, headers={"Retry-After": retry_after})
        assert request.url.path == "/authorities/names/n85362651.json"
        return httpx.Response(200, json=authority)

    monkeypatch.setattr(reads.asyncio, "sleep", no_wait)
    monkeypatch.setattr(reads, "authority_audit_client",
                        lambda: httpx.AsyncClient(transport=httpx.MockTransport(serve)))
    viaf_sources, lc_headings = await audit_job._read_cited_authority_sources(entities, ["Q10"])
    result = people_role_cases(record, manuscript, entities, viaf_sources=viaf_sources, lc_headings=lc_headings)
    assert result[0]["authority_verified"]
    assert requests == ["viaf.org"] * viaf_requests + ["id.loc.gov"]
    assert delays == ([2.0] if viaf_requests == 2 else [])

    conflicting_viaf = {"248556677": {"viafID": "248556677", "nameType": "Personal"}}
    assert not people_role_cases(
        record, manuscript, entities, viaf_sources=conflicting_viaf, lc_headings=lc_headings,
    )[0]["authority_verified"]
    assert not people_role_cases(
        record, manuscript, entities, viaf_sources={}, lc_headings={"n85362651": {"אבן צור, יעקב בן ראובן, 1673־1753"}},
    )[0]["authority_verified"]
    linked["references"].pop()
    assert not people_role_cases(
        record, manuscript, entities, viaf_sources=viaf_sources, lc_headings=lc_headings,
    )[0]["authority_verified"]


@pytest.mark.asyncio
async def test_viaf_retry_keeps_the_cluster_after_the_wait(monkeypatch):
    import httpx

    from app.pipeline import wikidata_gila_authority_reads as reads

    responses = [
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(200, json={"ns1:VIAFCluster": {"ns1:viafID": "248556677"}}),
    ]
    delays = []

    async def no_wait(seconds):
        delays.append(seconds)

    monkeypatch.setattr(reads.asyncio, "sleep", no_wait)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: responses.pop(0))) as client:
        assert await reads.read_viaf_clusters(client, {"248556677"}) == {
            "248556677": {"ns1:viafID": "248556677"},
        }
    assert delays == [3.0, 0.5]


def test_audit_verifies_dated_author_with_independent_authority():
    fields = _field("100", ("a", "Example, Leah"), ("d", "1690-1760")) + _field("245", ("a", "Book title"))
    record = parse_nli_records(_response(_record(fields)), {CONTROL})[CONTROL]
    person = {"id": "Q42", "claims": {"P31": [_claim({"id": "Q5"})], "P244": [_claim("n12345678")]}}
    manuscript = {"claims": {"P3959": [_claim(CONTROL)], "P1574": [_claim({"id": "Q88"}), _claim({"id": "Q88"})]}}
    author = _claim({"id": "Q42"})
    work = {"id": "Q88", "claims": {"P1476": [_claim({"text": "Book title", "language": "en"})], "P50": [author]}}
    entities = {"Q42": person, "Q88": work}
    headings = {"n12345678": {"Example, Leah, 1690-1760"}}

    assert people_role_cases(record, manuscript, entities, lc_headings=headings)[0]["authority_verified"]
    assert not people_role_cases(record, manuscript, entities,
                                 lc_headings={"n12345678": {"Example, Leah, 1690-1761"}})[0]["authority_verified"]
    author.pop("references")
    assert not people_role_cases(record, manuscript, entities, lc_headings=headings)[0]["authority_verified"]


def test_dated_repair_keeps_placeholder_and_rejects_conflicting_scribe():
    from app.pipeline.wikidata_gila_people import plan_dated_person_repair

    fields = _field("700", ("a", "Example, Leah"), ("d", "1690-1760"), ("4", "scr"))
    record = parse_nli_records(_response(_record(fields)), {CONTROL})[CONTROL]
    person = {"id": "Q42", "claims": {"P31": [_claim({"id": "Q5"})], "P244": [_claim("n12345678")]}}
    placeholder = _claim(None, qualifiers={"P1932": "Example, Leah"})
    placeholder["mainsnak"] = {"snaktype": "somevalue"}
    manuscript = {"id": "Q10", "claims": {"P3959": [_claim(CONTROL)], "P11603": [placeholder]}}
    case = {**people_role_cases(record, manuscript, {})[0], "control_number": CONTROL, "qid": "Q10"}
    headings = {"n12345678": {"Example, Leah, 1690-1760"}}

    target, statement, claim_id = plan_dated_person_repair(record, case, manuscript, {}, person, headings)
    assert (target, statement.property_id, statement.value, claim_id) == ("Q10", "P11603", "Q42", None)
    assert len(statement.reference_blocks) == 2
    assert statement.reference_blocks[1][0]["value"] == "https://id.loc.gov/authorities/names/n12345678.html"
    assert manuscript["claims"]["P11603"] == [placeholder]
    manuscript["claims"]["P11603"].append(_claim({"id": "Q43"}))
    assert plan_dated_person_repair(record, case, manuscript, {}, person, headings) is None
