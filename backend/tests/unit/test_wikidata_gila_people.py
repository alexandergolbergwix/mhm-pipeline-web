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
