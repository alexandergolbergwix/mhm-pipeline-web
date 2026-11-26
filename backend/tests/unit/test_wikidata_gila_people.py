"""Exercise the NLI import, exact role correspondence, and the scoped release."""

from types import SimpleNamespace

import pytest

from app.pipeline import wikidata_gila_audit_job as audit_job
from app.pipeline.wikidata_gila_authority_reads import MazalPersonAuthority
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
    viaf_sources, lc_headings, _ = await audit_job._read_cited_authority_sources(entities, ["Q10"])
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
    from app.pipeline.wikidata_gila_people import plan_person_role_repair

    fields = _field("700", ("a", "Example, Leah"), ("d", "1690-1760"), ("4", "scr"))
    record = parse_nli_records(_response(_record(fields)), {CONTROL})[CONTROL]
    person = {"id": "Q42", "claims": {"P31": [_claim({"id": "Q5"})], "P244": [_claim("n12345678")]}}
    placeholder = _claim(None, qualifiers={"P1932": "Example, Leah"})
    placeholder["mainsnak"] = {"snaktype": "somevalue"}
    manuscript = {"id": "Q10", "claims": {"P3959": [_claim(CONTROL)], "P11603": [placeholder]}}
    case = {**people_role_cases(record, manuscript, {})[0], "control_number": CONTROL, "qid": "Q10"}
    headings = {"n12345678": {"Example, Leah, 1690-1760"}}

    target, statement, claim_id = plan_person_role_repair(record, case, manuscript, {}, person, headings)
    assert (target, statement.property_id, statement.value, claim_id) == ("Q10", "P11603", "Q42", None)
    assert len(statement.reference_blocks) == 2
    assert statement.reference_blocks[1][0]["value"] == "https://id.loc.gov/authorities/names/n12345678.html"
    assert manuscript["claims"]["P11603"] == [placeholder]
    manuscript["claims"]["P11603"].append(_claim({"id": "Q43"}))
    assert plan_person_role_repair(record, case, manuscript, {}, person, headings) is None


def test_modena_scribe_uses_mazal_when_viaf_is_unavailable():
    fields = _field("700", ("a", "מודנה, יהודה אריה בן יצחק"), ("d", "1571-1648"), ("4", "scr"))
    record = parse_nli_records(_response(_record(fields)), {CONTROL})[CONTROL]
    identifier = "987007265574105171"
    person = {"id": "Q1652176", "claims": {
        "P31": [_claim({"id": "Q5"})], "P214": [_claim("4948231")], "P8189": [_claim(identifier)],
    }}
    link = _claim({"id": "Q1652176"}, qualifiers={"P1932": "מודנה, יהודה אריה בן יצחק"})
    link["references"].append({"snaks": {"P854": [_snak("https://viaf.org/viaf/4948231")]}})
    manuscript = {"claims": {"P3959": [_claim(CONTROL)], "P11603": [link]}}
    headings = {identifier: {"מודנה, יהודה אריה בן יצחק 1571-1648"}}

    def verified(mazal_headings=headings, viaf_sources=None):
        return people_role_cases(
            record, manuscript, {"Q1652176": person},
            viaf_sources=viaf_sources, mazal_headings=mazal_headings,
        )[0]["authority_verified"]

    assert verified()
    assert not verified({identifier: {"מודנה, יהודה אריה בן יצחק 1571-1649"}})
    assert not verified(viaf_sources={"4948231": {"viafID": "4948231", "nameType": "Personal"}})
    link["references"].pop()
    assert not verified()
    link["references"].append({"snaks": {"P854": [_snak(f"https://www.nli.org.il/he/a-topic/{identifier}")]}})
    assert verified()
    link["references"][0]["snaks"].clear()
    assert not verified()


def test_mazal_accepts_only_years_explicitly_listed_in_the_authority():
    from app.pipeline.wikidata_gila_people import dated_mazal_match

    identifier = "987007265574105171"
    person = {"claims": {"P31": [_claim({"id": "Q5"})], "P8189": [_claim(identifier)]}}
    headings = {identifier: {"Example Person 1542 or 1543-1620"}}
    assert dated_mazal_match(person, {"example person"}, ("1542", "1620"), headings)
    assert dated_mazal_match(person, {"example person"}, ("1543", "1620"), headings)
    assert not dated_mazal_match(person, {"example person"}, ("1541", "1620"), headings)


@pytest.mark.asyncio
async def test_scribe_without_dates_requires_exact_catalogue_authority_link():
    import httpx

    from app.pipeline.wikidata_gila_catalogue_authority import (
        catalogue_authority_url, cited_catalogue_identities, read_catalogue_authority_records,
    )
    from app.pipeline.wikidata_gila_people import plan_person_role_repair

    fields = _field("700", ("a", "Example, Leah"), ("4", "scr"))
    raw = parse_nli_records(_response(_record(fields)), {CONTROL})[CONTROL]
    identifier = "987007265574105171"
    person = {"id": "Q42", "claims": {"P31": [_claim({"id": "Q5"})], "P8189": [_claim(identifier)]}}
    manuscript = {"id": "Q10", "claims": {"P3959": [_claim(CONTROL)]}}
    case = {**people_role_cases(raw, manuscript, {})[0], "control_number": CONTROL, "qid": "Q10"}
    profiles = {identifier: MazalPersonAuthority(frozenset({"Example, Leah"}), "")}
    payload = _response(_record(fields)).replace(b"<records>", b"<numberOfRecords>1</numberOfRecords><records>")

    def response(request):
        assert request.url.params["query"] == f"(alma.mms_id={CONTROL}) and (alma.authority_id={identifier})"
        return httpx.Response(200, content=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        records = await read_catalogue_authority_records({(CONTROL, identifier)}, client=client)
    assert plan_person_role_repair(raw, case, manuscript, {}, person, {}, mazal_people=profiles) is None
    plan = plan_person_role_repair(
        raw, case, manuscript, {}, person, {}, mazal_people=profiles, catalogue_records=records,
    )
    assert plan[:1] == ("Q10",)
    url = catalogue_authority_url(CONTROL, identifier)
    assert plan[1].reference_blocks[-1][0]["value"] == url
    link = _claim({"id": "Q42"}, qualifiers={"P1932": "Example, Leah"})
    link["references"].append({"snaks": {"P854": [_snak(url)]}})
    assert cited_catalogue_identities(link) == {(CONTROL, identifier)}
    manuscript["claims"]["P11603"] = [link]
    args = {"mazal_people": profiles, "catalogue_records": records}
    assert people_role_cases(raw, manuscript, {"Q42": person}, **args)[0]["authority_verified"]

    same_name = parse_nli_records(_response(_record(fields + _field("600", ("a", "Example, Leah")))), {CONTROL})[CONTROL]
    args["catalogue_records"] = {(CONTROL, identifier): same_name}
    assert not people_role_cases(raw, manuscript, {"Q42": person}, **args)[0]["authority_verified"]


@pytest.mark.asyncio
async def test_catalogue_authority_check_rejects_unexpected_marc_control_number():
    import httpx

    from app.pipeline.wikidata_gila_catalogue_authority import read_catalogue_authority_records

    payload = _response(_record("", "990000403370205171"))
    payload = payload.replace(b"<records>", b"<numberOfRecords>1</numberOfRecords><records>")
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=payload))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await read_catalogue_authority_records({(CONTROL, "987007265574105171")}, client=client) == {}


def test_indexed_mazal_name_proves_one_scribe_and_preserves_the_other():
    from app.pipeline.wikidata_gila_people import catalogue_mazal_names, plan_person_role_repair

    fields = _field("700", ("a", "Example, Leah"), ("4", "scr")) + _field("700", ("a", "Example, Miriam"), ("4", "scr"))
    raw = parse_nli_records(_response(_record(fields)), {CONTROL})[CONTROL]
    identifier = "987007265574105171"
    person = {"id": "Q42", "claims": {"P31": [_claim({"id": "Q5"})], "P8189": [_claim(identifier)]}}
    companion = _claim({"id": "Q51"}, qualifiers={"P1932": "Example, Miriam"})
    manuscript = {"id": "Q10", "claims": {"P3959": [_claim(CONTROL)], "P11603": [companion]}}
    case = {**people_role_cases(raw, manuscript, {})[0], "control_number": CONTROL, "qid": "Q10"}
    preferred = {identifier: MazalPersonAuthority(frozenset({"Leah Marcella Example"}), "")}
    indexed = {identifier: MazalPersonAuthority(frozenset({"Leah Marcella Example"}), "", frozenset({"example leah"}))}
    catalogue = {(CONTROL, identifier): raw}
    assert catalogue_mazal_names([raw]) == {"example leah", "example miriam"}
    assert plan_person_role_repair(raw, case, manuscript, {}, person, {},
                                  mazal_people=preferred, catalogue_records=catalogue) is None
    plan = plan_person_role_repair(raw, case, manuscript, {}, person, {},
                                   mazal_people=indexed, catalogue_records=catalogue)
    assert plan[1].property_id == "P11603"
    assert plan[2] is None
    assert manuscript["claims"]["P11603"] == [companion]
    companion["references"] = []
    assert plan_person_role_repair(raw, case, manuscript, {}, person, {},
                                   mazal_people=indexed, catalogue_records=catalogue) is None


def test_added_author_without_title_needs_one_explicit_work():
    from app.pipeline.wikidata_gila_people import plan_person_role_repair

    fields = _field("245", ("a", "Book title")) + _field("700", ("a", "Example, Leah"), ("4", "aut"))
    raw = parse_nli_records(_response(_record(fields)), {CONTROL})[CONTROL]
    identifier = "987007265574105171"
    person = {"id": "Q42", "claims": {"P31": [_claim({"id": "Q5"})], "P8189": [_claim(identifier)]}}
    work = {"id": "Q88", "claims": {"P1476": [_claim({"text": "Book title", "language": "en"})]}}
    manuscript = {"id": "Q10", "claims": {"P3959": [_claim(CONTROL)], "P1574": [_claim({"id": "Q88"})]}}
    case = {**people_role_cases(raw, manuscript, {"Q88": work})[0], "control_number": CONTROL, "qid": "Q10"}
    profiles = {identifier: MazalPersonAuthority(frozenset({"Example, Leah"}), "")}

    def plan(source):
        return plan_person_role_repair(source, case, manuscript, {"Q88": work}, person, {},
                                       mazal_people=profiles, catalogue_records={(CONTROL, identifier): source})

    assert plan(raw)[0] == "Q88"
    composite = parse_nli_records(_response(_record(fields + _field("505", ("a", "A second text")))), {CONTROL})[CONTROL]
    assert plan(composite) is None
    manuscript["claims"]["P1574"].append(_claim({"id": "Q89"}))
    assert plan(raw) is None


def test_existing_catalogue_reference_is_reused_for_an_authority_repair():
    from app.pipeline.wikidata_gila_people import plan_person_role_repair

    record = parse_nli_records(_response(_record(_field("700", ("a", "Example, Leah"), ("4", "scr")))), {CONTROL})[CONTROL]
    identifier = "987007265574105171"
    person = {"id": "Q42", "claims": {"P31": [_claim({"id": "Q5"})], "P8189": [_claim(identifier)]}}
    existing = _claim({"id": "Q42"}, qualifiers={"P1932": "Example, Leah"})
    existing["id"] = "Q10$existing"
    manuscript = {"id": "Q10", "claims": {"P3959": [_claim(CONTROL)], "P11603": [existing]}}
    case = {**people_role_cases(record, manuscript, {"Q42": person})[0], "control_number": CONTROL, "qid": "Q10"}
    plan = plan_person_role_repair(record, case, manuscript, {}, person, {},
        mazal_people={identifier: MazalPersonAuthority(frozenset({"Example, Leah"}), "")},
        catalogue_records={(CONTROL, identifier): record})
    assert plan[2] == existing["id"]
    assert len(plan[1].reference_blocks) == 1
    assert [row["property"] for row in plan[1].reference_blocks[0]] == ["P854"]


def test_death_year_needs_exact_catalogue_match_and_both_citations():
    from app.pipeline.wikidata_gila_authority_reads import library_of_congress_death_year
    from app.pipeline.wikidata_gila_catalogue_authority import catalogue_authority_url
    from app.pipeline.wikidata_gila_people import plan_person_role_repair

    identifier = "987007265574105171"
    lccn = "no2008160185"
    mads = "http://www.loc.gov/mads/rdf/v1#"
    payload = [
        {"@id": f"http://id.loc.gov/authorities/names/{lccn}",
         "@type": [mads + "Authority", mads + "PersonalName"], mads + "hasSource": [{"@id": "_:linked"}]},
        {"@id": "_:linked", mads + "citationNote": [{"@value": "The copyist's note (d. 1661)"}]},
        {"@id": "_:unlinked", mads + "citationNote": [{"@value": "A different person (d. 1662)"}]},
    ]
    assert library_of_congress_death_year(payload, lccn) == "1661"
    assert library_of_congress_death_year(payload, "no2008160186") is None
    raw = parse_nli_records(_response(_record(_field(
        "700", ("a", "Example, Leah"), ("d", "נפטר 1661"), ("4", "scr"),
    ))), {CONTROL})[CONTROL]
    person = {"id": "Q42", "claims": {
        "P31": [_claim({"id": "Q5"})], "P8189": [_claim(identifier)], "P244": [_claim(lccn)],
        "P570": [_claim({"time": "+1661-01-01T00:00:00Z", "precision": 9, "before": 0, "after": 0,
                        "calendarmodel": "http://www.wikidata.org/entity/Q1985727"})],
    }}
    manuscript = {"id": "Q10", "claims": {"P3959": [_claim(CONTROL)]}}
    case = {**people_role_cases(raw, manuscript, {})[0], "control_number": CONTROL, "qid": "Q10"}
    proof = {
        "mazal_people": {identifier: MazalPersonAuthority(frozenset({"Example, Leah"}), "active 17th century")},
        "catalogue_records": {(CONTROL, identifier): raw},
    }
    assert not plan_person_role_repair(raw, case, manuscript, {}, person, {}, **proof)
    proof["lc_death_years"] = {lccn: "1661"}
    _, statement, _ = plan_person_role_repair(raw, case, manuscript, {}, person, {}, **proof)
    urls = [block[0]["value"] for block in statement.reference_blocks[1:]]
    assert urls == [catalogue_authority_url(CONTROL, identifier), f"https://id.loc.gov/authorities/names/{lccn}.html"]
    link = _claim({"id": "Q42"}, qualifiers={"P1932": "Example, Leah"})
    manuscript["claims"]["P11603"] = [link]
    link["references"].append({"snaks": {"P854": [_snak(urls[0])]}})
    assert not people_role_cases(raw, manuscript, {"Q42": person}, **proof)[0]["verified"]
    link["references"].append({"snaks": {"P854": [_snak(urls[1])]}})
    assert people_role_cases(raw, manuscript, {"Q42": person}, **proof)[0]["verified"]
    payload[1][mads + "citationNote"].append({"@value": "Another account (d. 1664)"})
    assert library_of_congress_death_year(payload, lccn) is None


def test_gnd_variant_supplies_exact_name_and_both_dates():
    from app.pipeline.wikidata_gila_people import plan_person_role_repair

    raw = parse_nli_records(_response(_record(_field(
        "700", ("a", "קמפר, יוהן"), ("d", "1670-1716"), ("4", "scr"),
    ))), {CONTROL})[CONTROL]
    identifier = "133857492"
    url = f"https://d-nb.info/gnd/{identifier}"
    person = {"id": "Q42", "claims": {"P31": [_claim({"id": "Q5"})], "P227": [_claim(identifier)]}}
    claim = _claim({"id": "Q42"}, qualifiers={"P1932": "קמפר, יוהן"})
    claim["id"] = "Q10$1"
    manuscript = {"id": "Q10", "claims": {"P3959": [_claim(CONTROL)], "P11603": [claim]}}
    case = {**people_role_cases(raw, manuscript, {"Q42": person})[0], "control_number": CONTROL, "qid": "Q10"}
    gnd = {"gndIdentifier": identifier, "id": url, "type": ["DifferentiatedPerson"], "dateOfBirth": ["1670"],
           "dateOfDeath": ["1716"], "preferredName": "Kemper, Johan", "variantName": ["קמפר, יוהן"]}
    target, statement, selected = plan_person_role_repair(
        raw, case, manuscript, {}, person, {}, gnd_sources={identifier: gnd},
    )
    assert (target, selected) == ("Q10", "Q10$1")
    assert [block[0]["value"] for block in statement.reference_blocks] == [url]
    claim["references"].append({"snaks": {"P854": [_snak(url)]}})
    assert people_role_cases(raw, manuscript, {"Q42": person}, gnd_sources={identifier: gnd})[0]["verified"]
    gnd["dateOfDeath"].append("1717")
    assert not people_role_cases(raw, manuscript, {"Q42": person}, gnd_sources={identifier: gnd})[0]["verified"]


def test_reviewed_catalogue_dates_require_the_exact_scribe_and_corroboration():
    from app.pipeline.wikidata_gila_catalogue_authority import REVIEWED_SCRIBE_SOURCES, catalogue_authority_url
    from app.pipeline.wikidata_gila_people import plan_person_role_repair

    cn = "990000569120205171"
    identifier = "987007298185505171"
    proof = REVIEWED_SCRIBE_SOURCES[cn, identifier]
    source = _field("700", ("a", proof.heading), ("d", proof.dates), ("e", "scribe"))
    raw = parse_nli_records(_response(_record(source, cn)), {cn})[cn]
    support = _record(_field("100", ("a", proof.heading), ("d", proof.dates)), proof.corroborating_control)
    extra = parse_nli_records(_response(support), {proof.corroborating_control})
    person = {"id": proof.person_qid, "claims": {"P31": [_claim({"id": "Q5"})], "P8189": [_claim(identifier)]}}
    manuscript = {"id": "Q10", "claims": {"P3959": [_claim(cn)]}}
    case = {**people_role_cases(raw, manuscript, {})[0], "control_number": cn, "qid": "Q10"}
    roles = {(cn, identifier): raw}
    profiles = {identifier: MazalPersonAuthority(frozenset({proof.heading}), "1688-1743")}

    def plan():
        return plan_person_role_repair(raw, case, manuscript, {}, person, {},
                                       mazal_people=profiles, catalogue_records=roles)

    assert plan() is None
    roles[proof.corroborating_control, identifier] = extra[proof.corroborating_control]
    _, statement, _ = plan()
    assert [block[0]["value"] for block in statement.reference_blocks[1:]] == [
        catalogue_authority_url(cn, identifier), catalogue_authority_url(proof.corroborating_control, identifier),
    ]
    person["id"] = "Q99"
    assert plan() is None
    person["id"] = proof.person_qid
    roles[cn, identifier] = parse_nli_records(_response(_record(source + source, cn)), {cn})[cn]
    assert plan() is None


def test_reviewed_colophon_resolves_only_its_exact_named_placeholder(monkeypatch):
    from hashlib import sha256

    from app.pipeline import wikidata_gila_scribe_colophons as colophons
    from app.pipeline.wikidata_gila_people import plan_person_role_repair

    name = colophons.DELFOGIT_HEADING
    note = "A reviewed colophon names the copyist."
    fields = _field("700", ("a", name), ("4", "scr"))
    record = parse_nli_records(_response(_record(fields + _field("957", ("a", note)))), {CONTROL})[CONTROL]
    unknown = _claim(None, qualifiers={"P1932": name})
    unknown["id"] = "Q10$reviewed"
    unknown["mainsnak"] = {"snaktype": "somevalue"}
    manuscript = {"id": "Q10", "claims": {"P3959": [_claim(CONTROL)], "P11603": [unknown]}}
    case = {**people_role_cases(record, manuscript, {})[0], "control_number": CONTROL, "qid": "Q10"}
    monkeypatch.setitem(colophons._COLOPHONS, CONTROL, (case["occurrence"], sha256(note.encode()).hexdigest()))
    occupation = _claim({"id": "Q916292"})
    occupation["references"] = [{"snaks": {"P854": [_snak(url)]}}
                                for url in (colophons.DELFOGIT_PENN, colophons.DELFOGIT_PARMA)]
    person = {"id": colophons.DELFOGIT_QID, "claims": {"P31": [_claim({"id": "Q5"})], "P106": [occupation]}}
    wrong_mazal = _claim(colophons.DELFOGIT_WRONG_IDS["P8189"])
    person["claims"]["P8189"] = [wrong_mazal]

    def plan(raw=record):
        return plan_person_role_repair(raw, case, manuscript, {}, person, {})

    assert plan() is None
    wrong_mazal["rank"] = "deprecated"
    target, statement, selected = plan()
    assert (target, selected) == ("Q10", unknown["id"])
    assert [block[0]["value"] for block in statement.reference_blocks] == [colophons.DELFOGIT_PENN]
    changed = parse_nli_records(_response(_record(fields + _field("957", ("a", "Changed note")))), {CONTROL})
    assert plan(changed[CONTROL]) is None
    unknown["mainsnak"] = _snak({"id": person["id"]})
    assert not people_role_cases(record, manuscript, {person["id"]: person})[0]["verified"]
    unknown["references"].append({"snaks": {"P854": [_snak(colophons.DELFOGIT_PENN)]}})
    assert people_role_cases(record, manuscript, {person["id"]: person})[0]["authority_verified"]


def test_reviewed_exemplar_requires_the_cited_deprecation_and_preserves_other_scribe(monkeypatch):
    from hashlib import sha256

    from app.pipeline import wikidata_gila_scribe_colophons as colophons

    name = colophons.DELFOGIT_HEADING
    actual_scribe = "מיליאב, עמנואל בן גד"
    colophon = "The actual scribe signed this copy."
    provenance = "The colophon refers to a different copy from the exemplar."
    fields = (
        _field("700", ("a", actual_scribe), ("4", "scr"))
        + _field("700", ("a", name), ("4", "scr"))
        + _field("957", ("a", colophon)) + _field("561", ("a", provenance))
    )
    record = parse_nli_records(_response(_record(fields)), {CONTROL})[CONTROL]
    old = _claim(None, qualifiers={"P1932": name, "P2241": {"id": colophons.MISATTRIBUTION}})
    old["mainsnak"] = {"snaktype": "somevalue"}
    keeper = _claim(None, qualifiers={"P1932": actual_scribe})
    keeper["mainsnak"] = {"snaktype": "somevalue"}
    manuscript = {"claims": {"P3959": [_claim(CONTROL)], "P11603": [old, keeper]}}

    def case():
        return next(row for row in people_role_cases(record, manuscript, {}) if row["source_value"] == name)

    monkeypatch.setattr(colophons, "COLUMBIA_EXEMPLAR", CONTROL)
    monkeypatch.setattr(colophons, "_EXEMPLAR_PROVENANCE", sha256(provenance.encode()).hexdigest())
    monkeypatch.setitem(colophons._COLOPHONS, CONTROL, (case()["occurrence"], sha256(colophon.encode()).hexdigest()))
    old["references"].append({"snaks": {"P854": [_snak(colophons.COLUMBIA_EXEMPLAR_URL)]}})
    assert not case()["verified"]
    old["rank"] = "deprecated"
    assert case()["verified"] and case()["reason"] == "verified_scribe_misattribution"
    assert not case()["authority_verified"] and not case()["role_verified"]
    manuscript["claims"]["P11603"].remove(keeper)
    assert not case()["verified"]


def test_milhaud_title_page_requires_the_exact_person_and_both_citations(monkeypatch):
    from hashlib import sha256

    from app.pipeline import wikidata_gila_scribe_colophons as reviewed
    from app.pipeline.wikidata_gila_people import plan_person_role_repair

    note = "Title page signed by the scribe."
    digest = sha256(sha256(note.encode()).hexdigest().encode()).hexdigest()
    record = parse_nli_records(_response(_record(
        _field("700", ("a", reviewed.MILHAUD_HEADING), ("4", "scr"))
        + _field("500", ("a", note)),
    )), {CONTROL})[CONTROL]
    unknown = _claim(None, qualifiers={"P1932": reviewed.MILHAUD_HEADING})
    unknown["id"] = "Q10$milhaud"
    unknown["mainsnak"] = {"snaktype": "somevalue"}
    manuscript = {"id": "Q10", "claims": {"P3959": [_claim(CONTROL)], "P11603": [unknown]}}
    case = {**people_role_cases(record, manuscript, {})[0], "control_number": CONTROL, "qid": "Q10"}
    monkeypatch.setitem(reviewed._MILHAUD_COLOPHONS, CONTROL, (case["occurrence"], "500", digest))
    person = {"id": reviewed.MILHAUD_QID, "claims": {
        "P31": [_claim({"id": "Q5"})], "P13311": [_claim(reviewed.MILHAUD_DS_ID)],
        "P973": [_claim(reviewed.MILHAUD_COLUMBIA)],
    }}

    def plan():
        return plan_person_role_repair(record, case, manuscript, {}, person, {})

    _, statement, claim_id = plan()
    assert claim_id == unknown["id"]
    assert [block[0]["value"] for block in statement.reference_blocks] == [
        reviewed.MILHAUD_COLUMBIA, reviewed.MILHAUD_DS_URL,
    ]
    person["id"] = "Q99"
    assert plan() is None
    person["id"] = reviewed.MILHAUD_QID
    wrong_authority = [_claim("Q1")]
    person["claims"]["P13311"] = wrong_authority
    assert plan() is None
    person["claims"]["P13311"] = [_claim(reviewed.MILHAUD_DS_ID)]
    unknown["mainsnak"] = _snak({"id": person["id"]})
    unknown["references"].append({"snaks": {"P854": [_snak(reviewed.MILHAUD_COLUMBIA)]}})
    assert not people_role_cases(record, manuscript, {person["id"]: person})[0]["verified"]
    unknown["references"].append({"snaks": {"P854": [_snak(reviewed.MILHAUD_DS_URL)]}})
    assert people_role_cases(record, manuscript, {person["id"]: person})[0]["authority_verified"]


def test_milhaud_misattribution_preserves_the_verified_delfogit_link(monkeypatch):
    from hashlib import sha256

    from app.pipeline import wikidata_gila_scribe_colophons as reviewed

    note = "Delpuget signed this colophon."
    digest = sha256(sha256(note.encode()).hexdigest().encode()).hexdigest()
    fields = (
        _field("700", ("a", reviewed.MILHAUD_HEADING), ("4", "scr"))
        + _field("700", ("a", reviewed.DELFOGIT_HEADING), ("4", "scr"))
        + _field("957", ("a", note))
    )
    record = parse_nli_records(_response(_record(fields)), {CONTROL})[CONTROL]
    old = _claim(None, qualifiers={"P1932": reviewed.MILHAUD_HEADING, "P2241": {"id": reviewed.MISATTRIBUTION}})
    old["mainsnak"] = {"snaktype": "somevalue"}
    old["rank"] = "deprecated"
    old["references"].append({"snaks": {"P854": [_snak(reviewed.COLUMBIA_DELFOGIT_URL)]}})
    keeper = _claim({"id": "Q99"}, qualifiers={"P1932": reviewed.DELFOGIT_HEADING})
    manuscript = {"claims": {"P3959": [_claim(CONTROL)], "P11603": [old, keeper]}}

    def case():
        return next(row for row in people_role_cases(record, manuscript, {}) if row["source_value"] == reviewed.MILHAUD_HEADING)

    monkeypatch.setattr(reviewed, "COLUMBIA_DELFOGIT", CONTROL)
    monkeypatch.setitem(reviewed._MILHAUD_COLOPHONS, CONTROL, (case()["occurrence"], "957", digest))
    assert not case()["verified"]
    keeper["mainsnak"] = _snak({"id": reviewed.DELFOGIT_QID})
    assert case()["verified"] and case()["reason"] == "verified_scribe_misattribution"
    assert not case()["authority_verified"] and not case()["role_verified"]


def test_reviewed_scribe_note_requires_exact_authority_and_placeholder(monkeypatch):
    from hashlib import sha256

    from app.pipeline import wikidata_gila_scribe_colophons as reviewed
    from app.pipeline.wikidata_gila_people import plan_person_role_repair

    name = "Reviewed name"
    old_name = "Old expanded name"
    note = "The signed colophon identifies the scribe."
    authority_url = "https://example.org/authority"
    digest = sha256(sha256(note.encode()).hexdigest().encode()).hexdigest()

    def source(text):
        return parse_nli_records(_response(_record(
            _field("700", ("a", name), ("4", "scr")) + _field("957", ("a", text)),
        )), {CONTROL})[CONTROL]

    record = source(note)
    old = _claim(None, qualifiers={"P1932": old_name})
    old["id"] = "Q10$reviewed"
    old["mainsnak"] = {"snaktype": "somevalue"}
    manuscript = {"id": "Q10", "claims": {"P3959": [_claim(CONTROL)], "P11603": [old]}}
    case = {**people_role_cases(record, manuscript, {})[0], "control_number": CONTROL, "qid": "Q10"}
    identity = reviewed.ReviewedScribePerson(
        "Q20", "שם", (authority_url,), identifier=("P227", "123456789"),
        wrong_ids=(("P8189", "987000000000005171"),),
    )
    monkeypatch.setitem(reviewed._EXTRA_SCRIBE_NOTES, CONTROL, reviewed.ReviewedScribeNote(
        identity, case["occurrence"], name, digest, former_heading=old_name,
    ))
    person = {"id": identity.qid, "labels": {"he": {"value": identity.hebrew_name}}, "claims": {
        "P31": [_claim({"id": "Q5"})], "P227": [_claim("123456789")],
    }}

    def plan(raw=record):
        return plan_person_role_repair(raw, case, manuscript, {}, person, {})

    _, statement, claim_id = plan()
    assert claim_id == old["id"]
    assert statement.qualifiers == [{"property": "P1932", "value": name, "type": "string"}]
    assert [block[0]["value"] for block in statement.reference_blocks] == [authority_url]
    assert plan(source(note + " Changed.")) is None
    unrelated_id = _claim("987000000000005171")
    person["claims"]["P8189"] = [unrelated_id]
    assert plan() is None
    unrelated_id["rank"] = "deprecated"
    assert plan() is not None

    old["mainsnak"] = _snak({"id": identity.qid})
    old["qualifiers"]["P1932"] = [_snak(name)]
    assert not people_role_cases(record, manuscript, {identity.qid: person})[0]["verified"]
    old["references"].append({"snaks": {"P854": [_snak(authority_url)]}})
    assert people_role_cases(record, manuscript, {identity.qid: person})[0]["authority_verified"]


@pytest.mark.parametrize(
    ("corrected", "folio"),
    [("Immanuel son of Gad", None), (None, "final unnumbered replacement leaf")],
)
def test_reviewed_scribe_requires_the_signed_name_or_exact_folio(monkeypatch, corrected, folio):
    from hashlib import sha256

    from app.pipeline import wikidata_gila_scribe_colophons as reviewed
    from app.pipeline.wikidata_gila_people import plan_person_role_repair

    heading = "Gad son of Immanuel"
    signed_name = corrected or heading
    note = "The colophon names the person who copied the reviewed material."
    url = "https://example.org/record"
    record = parse_nli_records(_response(_record(
        _field("700", ("a", heading), ("4", "scr")) + _field("957", ("a", note)),
    )), {CONTROL})[CONTROL]
    claim = _claim(None, qualifiers={"P1932": heading})
    claim["id"] = "Q10$copyist"
    claim["mainsnak"] = {"snaktype": "somevalue"}
    manuscript = {"id": "Q10", "claims": {"P3959": [_claim(CONTROL)], "P11603": [claim]}}
    case = {**people_role_cases(record, manuscript, {})[0], "control_number": CONTROL, "qid": "Q10"}
    identity = reviewed.ReviewedScribePerson("Q20", None, (url,), identifier=("P13311", "Q30"))
    monkeypatch.setitem(reviewed._EXTRA_SCRIBE_NOTES, CONTROL, reviewed.ReviewedScribeNote(
        identity, case["occurrence"], heading, sha256(sha256(note.encode()).hexdigest().encode()).hexdigest(),
        corrected_heading=corrected, folio=folio,
    ))
    signed_person = {"id": "Q20", "claims": {"P31": [_claim({"id": "Q5"})], "P13311": [_claim("Q30")]}}
    other_person = {"id": "Q21", "claims": {"P31": [_claim({"id": "Q5"})], "P13311": [_claim("Q30")]}}
    assert plan_person_role_repair(record, case, manuscript, {}, other_person, {}) is None
    _, change, claim_id = plan_person_role_repair(record, case, manuscript, {}, signed_person, {})
    assert claim_id == claim["id"]
    assert change.qualifiers == [
        {"property": pid, "value": value, "type": "string"}
        for pid, value in ([("P1932", signed_name), ("P958", folio)] if folio else [("P1932", signed_name)])
    ]

    claim["references"].append({"snaks": {"P854": [_snak(url)]}})
    claim["mainsnak"] = _snak({"id": "Q21"})
    assert not people_role_cases(record, manuscript, {"Q21": other_person})[0]["verified"]
    claim["mainsnak"] = _snak({"id": "Q20"})
    assert not people_role_cases(record, manuscript, {"Q20": signed_person})[0]["verified"]
    claim["qualifiers"]["P1932"] = [_snak(signed_name)]
    if folio:
        claim["qualifiers"]["P958"] = [_snak(folio)]
    result = people_role_cases(record, manuscript, {"Q20": signed_person})[0]
    assert result["authority_verified"]
    assert result["source_value"] == heading


def test_linked_person_edit_keeps_the_claim_and_closes_the_exact_role():
    from copy import deepcopy

    from app.pipeline.wikidata_gila_linked_people import ALMA, bib_url
    from app.pipeline.wikidata_gila_people_batch import patches_for_group, source_cases, verify_claims
    from collections import Counter
    from converter.wikidata.uploader import WikidataUploader

    aid = "987007265745205171"
    heading = "Source Person"
    fields = _field("700", ("a", heading), ("d", "1640-1700"), ("e", "scribe"))
    raw = parse_nli_records(_response(_record(fields)), {CONTROL})[CONTROL]
    person = {"id": "Q42", "claims": {"P31": [_claim({"id": "Q5"})], "P8189": [_claim(aid)]}}
    placeholder = {
        **_claim(None, qualifiers={"P1932": heading}),
        "type": "statement", "rank": "normal", "id": "Q10$old",
        "mainsnak": {"property": "P11603", "snaktype": "somevalue"},
    }
    manuscript = {"id": "Q10", "lastrevid": 100, "claims": {
        "P3959": [_claim(CONTROL)], "P11603": [placeholder], "P31": [_claim({"id": "Q87167"})],
    }}
    bib = {
        "@id": bib_url(CONTROL),
        "contributor": {"@id": f"{ALMA}/authorities/{aid}.jsonld", "label": f"{heading}, 1640-1700"},
    }
    initial = people_role_cases(raw, manuscript, {"Q42": person})
    assert not initial[0]["verified"]
    row = {**initial[0], "case_id": "case", "control_number": CONTROL, "qid": "Q10"}
    proof, = source_cases([row], {CONTROL: raw}, {CONTROL: bib})
    builder = WikidataUploader.__new__(WikidataUploader)
    builder._is_test = False
    patches, case_ids = patches_for_group(
        [(proof, person)], manuscript, lambda st: builder._build_claim(st).get_json(), Counter(),
    )
    assert case_ids == ["case"]
    assert patches[0]["id"] == placeholder["id"]
    assert patches[0]["references"][0] == placeholder["references"][0]
    assert patches[0]["qualifiers"] == placeholder["qualifiers"]
    after = deepcopy(manuscript)
    after.update(lastrevid=101)
    after["claims"]["P11603"] = deepcopy(patches)
    assert verify_claims(manuscript, after, 101, patches)
    assert people_role_cases(raw, after, {"Q42": person}, linked_bibs={CONTROL: bib})[0]["verified"]
    assert not people_role_cases(raw, after, {"Q42": person})[0]["verified"]
    assert not list(source_cases([row], {CONTROL: raw}, {
        CONTROL: {**bib, "contributor": {**bib["contributor"], "label": f"{heading}, 1640-1701"}},
    }))
    assert not list(source_cases([row], {CONTROL: raw}, {
        CONTROL: {**bib, "@id": bib_url("990000403370205171")},
    }))
    after["claims"].pop("P31")
    assert not verify_claims(manuscript, after, 101, patches)


def test_complete_readback_accepts_only_the_generated_claim_id():
    from copy import deepcopy

    from app.pipeline.wikidata_gila_people_batch import verify_claims

    claim = {"type": "statement", "rank": "normal", "mainsnak": {
        "property": "P50", **_snak({"id": "Q42"}),
    }}
    before = {"id": "Q10", "lastrevid": 21, "claims": {"P31": [_claim({"id": "Q386724"})]}}
    after = deepcopy(before)
    after["lastrevid"] = 22
    after["claims"]["P50"] = [{**claim, "id": "Q10$new"}]
    assert verify_claims(before, after, 22, [claim])
    after["claims"]["P50"].append({**claim, "id": "Q10$duplicate"})
    assert not verify_claims(before, after, 22, [claim])


def test_bibframe_exact_owner_role_and_viaf_authority_close_the_audit_case():
    from collections import Counter
    from copy import deepcopy

    from app.pipeline.wikidata_gila_bibframe_people import frame_url, parse_frame
    from app.pipeline.wikidata_gila_linked_people import ALMA
    from app.pipeline.wikidata_gila_mazal_viaf import cluster_keys, viaf_mazal_url
    from app.pipeline.wikidata_gila_people_batch import patches_for_group, plan_groups, source_cases
    from converter.wikidata.uploader import WikidataUploader

    aid = "987007265745205171"
    heading = "Source Owner"
    raw = parse_nli_records(_response(_record(_field("700", ("a", heading), ("e", "former owner")))), {CONTROL})[CONTROL]
    catalogue = f"https://eu01.alma.exlibrisgroup.com/bf"
    work = f"{catalogue}/works/{CONTROL}?institute=972NNL_INST"
    instance = f"{catalogue}/instances/{CONTROL}?institute=972NNL_INST"
    authority = f"{ALMA}/authorities/{aid}.jsonld"
    xml = f"""
    <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns:bf="http://id.loc.gov/ontologies/bibframe/"
        xmlns:bflc="http://id.loc.gov/ontologies/bflc/"
        xmlns:mads="http://www.loc.gov/mads/rdf/v1#">
      <bf:Work rdf:about="{work}">
        <bf:hasInstance rdf:resource="{instance}" />
        <bf:contribution><bf:Contribution><bf:agent><bf:Agent rdf:about="{authority}">
          <rdf:type rdf:resource="http://id.loc.gov/ontologies/bibframe/Person" />
          <bflc:marcKey>700  $a{heading}$eformer owner</bflc:marcKey>
          <mads:isIdentifiedByAuthority rdf:resource="{authority}" />
        </bf:Agent></bf:agent></bf:Contribution></bf:contribution>
      </bf:Work>
      <bf:Instance rdf:about="{instance}" />
    </rdf:RDF>
    """.encode()
    frames = {CONTROL: parse_frame(CONTROL, xml)}
    entity = {"id": "Q10", "lastrevid": 1, "claims": {"P3959": [_claim(CONTROL)]}}
    before = people_role_cases(raw, entity, {})
    case = {**before[0], "case_id": "owner", "qid": "Q10", "control_number": CONTROL}
    assert not list(source_cases([case], {CONTROL: raw}, {}))
    changed = xml.replace(b"$eformer owner", b"$escribe")
    assert not list(source_cases([case], {CONTROL: raw}, {}, {CONTROL: parse_frame(CONTROL, changed)}))
    assert not parse_frame("990000403370205171", xml)
    proof, = source_cases([case], {CONTROL: raw}, {}, frames)
    assert proof.source_url == frame_url(CONTROL)

    cluster = {
        "ns1:nameType": "Personal", "ns1:viafID": "123456",
        "ns1:sources": {"ns1:source": [{"content": f"J9U|{aid}"}, {"content": "LC|n  12345678"}]},
    }
    keys = cluster_keys(aid, cluster)
    assert keys == {("P214", "123456"), ("P244", "n 12345678")}
    assert not cluster_keys("987007265745905171", cluster)
    person = {"id": "Q42", "claims": {"P31": [_claim({"id": "Q5"})], "P244": [_claim("n12345678")]}}
    people = {"Q42": person}
    counts = Counter()
    groups = plan_groups([proof], {aid: "Q42"}, {"Q10": entity, **people}, counts, {aid: keys})
    builder = WikidataUploader.__new__(WikidataUploader)
    builder._is_test = False
    patches, ids = patches_for_group(groups["Q10"], entity, lambda st: builder._build_claim(st).get_json(), counts)
    assert ids == ["owner"]
    urls = [v["datavalue"]["value"] for v in patches[0]["references"][0]["snaks"]["P854"]]
    assert set(urls) == {frame_url(CONTROL), viaf_mazal_url(aid)}
    final = deepcopy(entity)
    final["claims"]["P3342"] = patches
    assert people_role_cases(raw, final, people, linked_frames=frames, linked_viaf={aid: keys})[0]["verified"]
    assert not people_role_cases(raw, final, people, linked_frames=frames)[0]["verified"]
    person["claims"]["P8189"] = [_claim("987007265745905171")]
    assert not people_role_cases(raw, final, people, linked_frames=frames, linked_viaf={aid: keys})[0]["verified"]


def test_reviewed_person_keeps_credit_when_viaf_cannot_supply_evidence():
    from app.pipeline.wikidata_gila_linked_people import ALMA, bib_url
    from app.pipeline.wikidata_gila_mazal_viaf import viaf_mazal_url

    aid = "987007583337405171"
    heading = "Eustachio, Giovanni Paulo"
    raw = parse_nli_records(_response(_record(_field(
        "700", ("a", heading), ("e", "mentioned"),
    ))), {CONTROL})[CONTROL]
    bibs = {CONTROL: {
        "@id": bib_url(CONTROL),
        "contributor": {"@id": f"{ALMA}/authorities/{aid}", "label": heading},
    }}
    person = {"id": "Q105644274", "claims": {
        "P31": [_claim({"id": "Q5"})], "P214": [_claim("268998259")], "P268": [_claim("17163680d")],
    }}
    claim = _claim({"id": person["id"]}, qualifiers={"P1932": heading})
    claim["references"][0]["snaks"]["P854"] = [_snak(bib_url(CONTROL)), _snak(viaf_mazal_url(aid))]
    manuscript = {"id": "Q10", "claims": {"P3959": [_claim(CONTROL)], "P3342": [claim]}}

    def verified(viaf):
        case, = people_role_cases(raw, manuscript, {person["id"]: person}, linked_bibs=bibs, linked_viaf=viaf)
        return case["verified"]

    assert verified({aid: {("P214", "268998259")}})
    assert verified({})
    assert not verified({aid: frozenset()})
    assert not verified({aid: {("P214", "999999")}})
    person["claims"]["P8189"] = [_claim("987007265745905171")]
    assert not verified({})
    del person["claims"]["P8189"]
    person["claims"]["P268"] = [_claim("00000000x")]
    assert not verified({})
    person["claims"]["P268"] = [_claim("17163680d")]
    claim["references"][0]["snaks"]["P854"].pop()
    assert not verified({})


@pytest.mark.asyncio
async def test_dated_mazal_batch_requires_one_preferred_name_and_exact_years(monkeypatch):
    from unittest.mock import AsyncMock

    from app.pipeline import wikidata_gila_people_batch as batch
    from app.pipeline.wikidata_gila_authority_reads import mazal_authority_url

    raw = parse_nli_records(_response(_record(_field(
        "700", ("a", "Qualified Scribe"), ("d", "1612-1678"), ("e", "scribe"),
    ))), {CONTROL})[CONTROL]
    manuscript = {"id": "Q10", "claims": {"P3959": [_claim(CONTROL)]}}
    role = people_role_cases(raw, manuscript, {})[0]
    case = {**role, "case_id": "scribe", "qid": "Q10", "control_number": CONTROL}
    aid = "987007265745205171"
    index = AsyncMock(return_value={("qualified scribe", "1612", "1678"): {aid}})
    monkeypatch.setattr(batch, "read_named_mazal_people", index)
    proof, = await batch.dated_source_cases([case], {CONTROL: raw}, {}, {})
    assert proof.dated_authority and proof.source_url == mazal_authority_url(aid)
    index.assert_awaited_once_with({"qualified scribe"})
    index.return_value = {("qualified scribe", "1612", "1678"): {aid, "987000265745205171"}}
    assert not await batch.dated_source_cases([case], {CONTROL: raw}, {}, {})
    index.return_value = {("qualified scribe", "1612", "1679"): {aid}}
    assert not await batch.dated_source_cases([case], {CONTROL: raw}, {}, {})


def test_linked_author_reuses_the_full_marc_heading_and_keeps_its_qualifier():
    from app.pipeline.wikidata_gila_linked_people import bib_url
    from app.pipeline.wikidata_gila_people_batch import repair_claim_builder, scoped_claim, source_cases

    record = parse_nli_records(_response(_record(
        _field("100", ("a", "Isaac ben Abraham,"), ("c", "of Troki,"), ("d", "1533-1594"))
        + _field("245", ("a", "Faith Strengthened"))
    )), {CONTROL})[CONTROL]
    aid = "987007297464205171"
    bib = {"@id": bib_url(CONTROL), "creator": [{
        "@id": bib_url(CONTROL).split("/bibs/")[0] + f"/authorities/{aid}",
        "label": "Isaac ben Abraham of Troki 1533-1594",
    }]}
    case = next(source_cases([{
        "case_id": "author", "control_number": CONTROL, "qid": "Q10", "occurrence": "100:1",
        "person_role": "author", "source_value": "Isaac ben Abraham",
    }], {CONTROL: record}, {CONTROL: bib}))
    person = {"id": "Q168171", "claims": {
        "P31": [_claim({"id": "Q5"})], "P8189": [_claim(aid)],
    }}
    old = {"id": "Q11$author", **_claim({"id": "Q168171"}, qualifiers={"P1932": "Isaac ben Abraham of Troki"})}
    target = {"id": "Q11", "claims": {"P50": [old]}}
    patch, error = scoped_claim(case, person, target, repair_claim_builder())
    assert error is None
    assert patch["id"] == old["id"]
    assert patch["qualifiers"] == old["qualifiers"]
    assert patch["references"][0] == old["references"][0]
    assert len(patch["references"]) == 2


def test_reviewed_author_identity_refuses_a_conflicting_authority_id():
    from app.pipeline.wikidata_gila_reviewed_people import reviewed_linked_person_url

    candidate = {"id": "Q59628092", "claims": {
        "P31": [_claim({"id": "Q5"})], "P244": [_claim("n86814129")],
        "P8189": [_claim("987007310194705171"), _claim("987007263551105171")],
    }}
    assert reviewed_linked_person_url(candidate, "987007310194705171") == (
        "https://id.loc.gov/authorities/names/n86814129.html"
    )
    candidate["claims"]["P8189"].append(_claim("987007297464205171"))
    assert reviewed_linked_person_url(candidate, "987007310194705171") is None


def test_reviewed_primary_author_requires_the_same_work_and_cited_identity():
    from app.pipeline.wikidata_gila_people_batch import repair_claim_builder, scoped_claim, source_cases, target_for

    cn = "990000798840205171"
    fields = _field("100", ("a", "אזובי, שלמה בן יהודה")) + _field("245", ("a", "אגודת אזוב"))
    record = parse_nli_records(_response(_record(fields, cn)), {cn})[cn]
    case = {"case_id": "author", "control_number": cn, "qid": "Q10", "occurrence": "100:1",
            "person_role": "author", "source_value": "אזובי, שלמה בן יהודה"}
    proof, = source_cases([case], {cn: record}, {})
    person = {"id": "Q107059242", "claims": {"P31": [_claim({"id": "Q5"})], "P269": [_claim("067065791")]}}
    work = {"id": "Q141755114", "claims": {"P1476": [_claim({"text": "אגודת אזוב", "language": "he"})]}}
    link = _claim({"id": work["id"]})
    link["references"][0]["snaks"]["P3959"][0] = _snak(cn)
    manuscript = {"id": "Q10", "claims": {"P3959": [_claim(cn)], "P1574": [link]}}
    entities = {work["id"]: work, person["id"]: person}
    assert target_for(proof, manuscript, entities) is work
    assert not people_role_cases(record, manuscript, entities)[0]["verified"]
    claim, reason = scoped_claim(proof, person, work, repair_claim_builder())
    assert reason is None
    work["claims"]["P50"] = [claim]
    assert people_role_cases(record, manuscript, entities)[0]["verified"]
    person["claims"]["P269"] = [_claim("012345678")]
    assert not people_role_cases(record, manuscript, entities)[0]["verified"]
    assert not target_for(proof, manuscript, {work["id"]: {**work, "id": "Q11"}})


def test_reviewed_work_title_requires_the_unique_source_link():
    from app.pipeline.wikidata_gila_people import _author_target, _person_occurrences

    cn = "990001722330205171"
    fields = _field("100", ("a", "Fonseca, Isaac Aboab da")) + _field("245", ("a", "Sermão."))
    record = parse_nli_records(_response(_record(fields, cn)), {cn})[cn]
    row = _person_occurrences(record)[0][0]
    work = {"id": "Q141755330", "claims": {"P1476": [_claim({"text": "דרשה", "language": "he"})]}}
    link = _claim({"id": work["id"]})
    link["references"][0]["snaks"]["P3959"] = [_snak(cn)]
    manuscript = {"id": "Q10", "claims": {"P1574": [link]}}
    assert _author_target(row, record, manuscript, {work["id"]: work}, cn) is work
    manuscript["claims"]["P1574"].append(_claim({"id": "Q42"}))
    assert not _author_target(row, record, manuscript, {work["id"]: work}, cn)


@pytest.mark.parametrize(("title", "genre", "accepted"), [
    ("אגרת אל הנמען", "Personal correspondence", True),
    ("ספר הדרושים", "Personal correspondence", False),
    ("אגרת אל הנמען", "Autograph manuscripts", False),
])
def test_recipient_needs_the_exact_role_on_a_letter(title, genre, accepted):
    from app.pipeline.wikidata_gila_linked_people import bib_url
    from app.pipeline.wikidata_gila_people_batch import source_cases

    fields = _field("700", ("a", "Source Recipient"), ("e", "(אליו)"))
    fields += _field("245", ("a", title)) + _field("655", ("a", genre))
    record = parse_nli_records(_response(_record(fields)), {CONTROL})[CONTROL]
    url = bib_url(CONTROL)
    bib = {"@id": url, "contributor": [{"label": "Source Recipient",
           "@id": url.split("/bibs/")[0] + "/authorities/987007265745205171"}]}
    case = {"case_id": "recipient", "qid": "Q10", "control_number": CONTROL,
            "occurrence": "700:1", "person_role": "recipient", "source_value": "Source Recipient"}
    assert bool(list(source_cases([case], {CONTROL: record}, {CONTROL: bib}))) is accepted


@pytest.mark.asyncio
async def test_gnd_retries_a_disconnected_read(monkeypatch):
    from unittest.mock import AsyncMock

    import httpx

    from app.pipeline import wikidata_gila_gnd_evidence as gnd

    response = httpx.Response(200)
    get = AsyncMock(side_effect=[httpx.RemoteProtocolError("disconnected"), response])
    wait = AsyncMock()
    monkeypatch.setattr(gnd.asyncio, "sleep", wait)
    result = await gnd._read_record(SimpleNamespace(get=get), "142126152")
    assert result is response
    assert get.await_count == 2
    wait.assert_awaited_once_with(1)


def test_reviewed_author_correction_retains_the_old_statement_and_requires_exact_evidence():
    from hashlib import sha256

    from app.pipeline.wikidata_gila_execution import semantic_entity
    from app.pipeline.wikidata_gila_reviewed_people import ReviewedWorkAuthor, reviewed_author_text_patch
    from app.pipeline.wikidata_source_choices import digest

    prior = {"id": "Q10$old", "rank": "normal", **_claim("Earlier author")}
    note = "The earlier author annotated this manuscript."
    record = parse_nli_records(_response(_record(_field("500", ("a", note)))), {CONTROL})[CONTROL]
    proof = ReviewedWorkAuthor(
        "Q10", "Correct author", None,
        retired_text_id=prior["id"],
        retired_text_digest=digest(semantic_entity(prior)),
        role_note_digest=sha256(note.encode()).hexdigest(),
    )
    target = {"id": "Q10", "claims": {"P2093": [prior]}}
    assert reviewed_author_text_patch(proof, record, target) == {**prior, "rank": "deprecated"}
    assert prior["rank"] == "normal"

    with pytest.raises(ValueError, match="changed"):
        reviewed_author_text_patch(proof, {"001": CONTROL}, target)
    prior["references"] = []
    with pytest.raises(ValueError, match="changed"):
        reviewed_author_text_patch(proof, record, target)
