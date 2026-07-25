"""Regression tests for codicological-unit Wikibase label/description quality."""

from __future__ import annotations

from rdflib import RDF, RDFS, URIRef

from converter.config.namespaces import HM, LRMOO
from converter.rdf.graph_builder import GraphBuilder
from converter.transformer.field_handlers import ExtractedData
from converter.wikibase.hmo_exporter import HmoWikibaseExporter

GENERIC_SUFFIX = "in the Hebrew Manuscripts Ontology (HMO)"


def _export_codicological_units(title: str = "ספר תהילים", shelfmark: str = "Heb. 12.34") -> list:
    data = ExtractedData(title=title, shelfmark=shelfmark)
    graph = GraphBuilder().build_graph(data, "990001800310205171")
    drafts = HmoWikibaseExporter().from_graph(graph)
    return [draft for draft in drafts if draft.entity_type == "Codicological_Unit"]


def test_main_codicological_unit_has_meaningful_description() -> None:
    title = "ספר תהילים"
    shelfmark = "Heb. 12.34"
    units = _export_codicological_units(title=title, shelfmark=shelfmark)

    assert len(units) == 1
    unit = units[0]
    assert unit.labels["en"] == "Main codicological unit of MS 990001800310205171"
    description = unit.descriptions["en"]
    assert GENERIC_SUFFIX not in description
    assert "990001800310205171" in description
    assert title in description
    assert shelfmark in description
    assert description.startswith("Primary codicological unit of manuscript")


def test_codicological_unit_rdf_comment_is_stamped() -> None:
    graph = GraphBuilder().build_graph(
        ExtractedData(title="ספר תהילים", shelfmark="Heb. 12.34"),
        "990001800310205171",
    )
    cu_nodes = list(graph.subjects(RDF.type, HM.Codicological_Unit))
    assert len(cu_nodes) == 1
    comment = str(graph.value(cu_nodes[0], RDFS.comment))
    assert GENERIC_SUFFIX not in comment
    assert "Primary codicological unit of manuscript 990001800310205171" in comment


def test_primary_entities_avoid_generic_wikibase_descriptions() -> None:
    data = ExtractedData(
        title="ספר תהילים",
        shelfmark="Heb. 12.34",
        authors=[{"name": "אהרן בן אליהו", "role": "author"}],
        place="ירושלים",
    )
    graph = GraphBuilder().build_graph(data, "990001800310205171")
    drafts = HmoWikibaseExporter().from_graph(graph)

    by_type = {draft.entity_type: draft for draft in drafts}
    for entity_type in (
        "F4_Manifestation_Singleton",
        "F1_Work",
        "F2_Expression",
        "E21_Person",
        "E53_Place",
        "E12_Production",
    ):
        draft = by_type.get(entity_type)
        assert draft is not None, entity_type
        description = draft.descriptions.get("en", "")
        assert GENERIC_SUFFIX not in description, (entity_type, description)
        if entity_type not in {"F1_Work", "E53_Place"}:
            assert "990001800310205171" in description


def test_expression_label_omits_in_ms_suffix() -> None:
    title = "ספר תהילים"
    graph = GraphBuilder().build_graph(
        ExtractedData(title=title, shelfmark="Heb. 12.34"),
        "990001800310205171",
    )
    expr_nodes = list(graph.subjects(RDF.type, LRMOO.F2_Expression))
    assert expr_nodes
    label = str(graph.value(expr_nodes[0], RDFS.label))
    assert "(in MS" not in label
    assert title in label


def test_production_event_has_substantive_label(monkeypatch) -> None:
    from converter.config.namespaces import CIDOC

    data = ExtractedData(
        title="ספר תהילים",
        place="ירושלים",
        dates={"year": 1460},
        contributors=[{"name": "אהרן הסופר", "role": "scribe"}],
    )
    graph = GraphBuilder().build_graph(data, "990001800310205171")
    prod_nodes = list(graph.subjects(RDF.type, CIDOC.E12_Production))
    assert prod_nodes
    label = str(graph.value(prod_nodes[0], RDFS.label))
    assert label.startswith("Production of MS 990001800310205171")
    assert "1460" in label


def test_time_span_label_is_not_bare_year() -> None:
    from converter.config.namespaces import CIDOC

    data = ExtractedData(title="ספר תהילים", dates={"year": 1460})
    graph = GraphBuilder().build_graph(data, "990001800310205171")
    span_nodes = list(graph.subjects(RDF.type, CIDOC["E52_Time-Span"]))
    assert span_nodes
    label = str(graph.value(span_nodes[0], RDFS.label))
    assert label == "Production period 1460"


def test_manuscript_label_has_hebrew_title_and_english_shelfmark() -> None:
    data = ExtractedData(title="ספר תהילים", shelfmark="Heb. 12.34")
    graph = GraphBuilder().build_graph(data, "990001800310205171")
    ms_nodes = list(graph.subjects(RDF.type, LRMOO.F4_Manifestation_Singleton))
    assert ms_nodes
    labels = {lit.language: str(lit) for lit in graph.objects(ms_nodes[0], RDFS.label)}
    assert labels.get("he") == "ספר תהילים"
    assert labels.get("en") == "Jerusalem, NLI, Heb. 12.34"


def test_manuscript_short_title_falls_back_to_shelfmark() -> None:
    data = ExtractedData(title="אב", shelfmark="Heb. 12.34")  # too-short title
    graph = GraphBuilder().build_graph(data, "990001800310205171")
    ms_nodes = list(graph.subjects(RDF.type, LRMOO.F4_Manifestation_Singleton))
    assert ms_nodes
    label_texts = {str(lit) for lit in graph.objects(ms_nodes[0], RDFS.label)}
    # Latin "MS <shelfmark>" fallback is used instead of the 2-char title.
    assert "MS Heb. 12.34" in label_texts
    assert "אב" not in label_texts


def test_text_tradition_latin_title_uses_en_label() -> None:
    data = ExtractedData(
        title="Diodati Segre",
        contents=[{"title": "Diodati Segre commentary", "sequence": 1}],
    )
    builder = GraphBuilder(add_philological_overlay=True)
    graph = builder.build_graph(data, "990001800310205171")
    trad_nodes = list(graph.subjects(RDF.type, HM.TextTradition))
    assert trad_nodes
    for node in trad_nodes:
        for lit in graph.objects(node, RDFS.label):
            # A Latin tradition title must never be tagged Hebrew.
            assert lit.language == "en"


def _production_draft(data: ExtractedData, cn: str = "990001800310205171"):
    graph = GraphBuilder().build_graph(data, cn)
    drafts = HmoWikibaseExporter().from_graph(graph)
    return next((d for d in drafts if d.entity_type == "E12_Production"), None)


def test_empty_production_description_states_negative_not_repeat(cn="990001800310205171") -> None:
    data = ExtractedData(title="ספר תהילים", shelfmark="Heb. 12.34")
    draft = _production_draft(data, cn)
    assert draft is not None
    desc = draft.descriptions["en"]
    assert "not recorded in the catalog record" in desc
    assert "Heb. 12.34" in desc
    assert desc != f"Production event for manuscript {cn}."


def test_paleographical_unit_comment_grounds_scribe() -> None:
    data = ExtractedData(
        title="ספר תהילים",
        shelfmark="Heb. 12.34",
        contributors=[{"name": "משה בן מימון", "role": "scribe"}],
    )
    graph = GraphBuilder().build_graph(data, "990001800310205171")
    pu_nodes = list(graph.subjects(RDF.type, HM.Paleographical_Unit))
    assert pu_nodes
    comment = str(graph.value(pu_nodes[0], RDFS.comment))
    assert "scribe משה בן מימון" in comment


def test_paleographical_unit_comment_states_negative_when_unknown() -> None:
    data = ExtractedData(title="ספר תהילים", shelfmark="Heb. 12.34")
    graph = GraphBuilder().build_graph(data, "990001800310205171")
    pu_nodes = list(graph.subjects(RDF.type, HM.Paleographical_Unit))
    assert pu_nodes
    comment = str(graph.value(pu_nodes[0], RDFS.comment))
    assert "not recorded in the catalog" in comment


def test_no_synthetic_tradition_for_unidentified_content() -> None:
    data = ExtractedData(shelfmark="Heb. 12.34")
    graph = GraphBuilder().build_graph(data, "990001800310205171")
    traditions = list(graph.subjects(RDF.type, HM.TextTradition))
    assert traditions == []


def test_long_isbd_work_label_uses_precolon_head() -> None:
    long_title = (
        "שער שברי לוחות : פירוש המסורת אשר חבר החכם השלם הרב הגדול "
        "לפרש את כל דקדוקי המקרא ואת כל טעמיו והוא חיבור נפלא מאד"
    )
    data = ExtractedData(title=long_title, shelfmark="Heb. 12.34")
    graph = GraphBuilder().build_graph(data, "990001800310205171")
    drafts = HmoWikibaseExporter().from_graph(graph)
    work = next((d for d in drafts if d.entity_type == "F1_Work"), None)
    assert work is not None
    label = work.labels.get("he") or work.labels.get("en")
    assert label == "שער שברי לוחות"
    assert "שער שברי לוחות" in work.descriptions["en"]


def test_digital_urls_strip_marc_quote_wrappers() -> None:
    from rdflib import URIRef
    from rdflib.namespace import XSD
    data = ExtractedData(
        title="ספר תהילים",
        digital_url='"https://example.org/manuscript',
        iiif_manifest_url="'https://example.org/manifest'",
    )
    graph = GraphBuilder().build_graph(data, "990001800310205171")
    ms = URIRef("https://w3id.org/mhm/ontology#MS_990001800310205171")
    assert str(graph.value(ms, HM.has_digital_representation_url)) == "https://example.org/manuscript"
    digital = next(graph.subjects(RDF.type, HM.DigitalAccess))
    assert str(graph.value(digital, HM.digital_access_url)) == "https://example.org/manuscript"
    assert graph.value(digital, HM.digital_access_url).datatype == XSD.anyURI
    assert str(graph.value(digital, HM.iiif_manifest_url)) == "https://example.org/manifest"


def test_restriction_url_strips_marc_quote_wrappers() -> None:
    from rdflib.namespace import XSD
    dirty = (
        '"http://web.nli.org.il/sites/NLI/Hebrew/library/'
        'items-terms-of-use/Pages/nli-public-domain.aspx"'
    )
    data = ExtractedData(
        title="ספר תהילים",
        rights_statement="Public domain",
        usage_restriction="Public domain",
        restriction_url=dirty,
    )
    graph = GraphBuilder().build_graph(data, "990001800310205171")
    ur = next(graph.subjects(RDF.type, HM.UsageRestriction))
    cleaned = str(graph.value(ur, HM.restriction_url))
    assert cleaned == (
        "http://web.nli.org.il/sites/NLI/Hebrew/library/"
        "items-terms-of-use/Pages/nli-public-domain.aspx"
    )
    assert cleaned[0] != '"'
    assert graph.value(ur, HM.restriction_url).datatype == XSD.anyURI


def test_anthology_uses_canonical_structure_type() -> None:
    data = ExtractedData(
        title="ספר תהילים",
        is_anthology=True,
        contents=[
            {"title": "שיר ראשון", "sequence": 1},
            {"title": "שיר שני", "sequence": 2},
        ],
    )
    graph = GraphBuilder().build_graph(data, "990001800310205171")
    ms = URIRef("https://w3id.org/mhm/ontology#MS_990001800310205171")
    assert (ms, RDF.type, HM.AnthologyStructure) in graph
    assert not any(str(predicate).endswith("has_anthology_structure") for _, predicate, _ in graph)
    assert not any(str(predicate).endswith("number_of_works") for _, predicate, _ in graph)
