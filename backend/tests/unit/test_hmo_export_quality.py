"""HMO export quality gate tests."""

from __future__ import annotations

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from converter.config.namespaces import HM, LRMOO
from converter.wikibase.hmo_export_quality import audit_entity_draft, audit_entity_drafts
from converter.wikibase.hmo_exporter import HmoWikibaseExporter
from converter.wikibase.models import WikibaseEntityDraft


def test_audit_flags_work_without_manuscript_scope() -> None:
    draft = WikibaseEntityDraft(
        local_id="QDraft_Work_test",
        labels={"he": "תורה"},
        descriptions={"en": "Literary work 'תורה'"},
        entity_type="F1_Work",
        class_uri=str(LRMOO.F1_Work),
        source_uri="http://example/work",
        statements=[],
        control_numbers=["990001"],
    )
    issues = audit_entity_draft(draft)
    assert any(i.code == "work_missing_manuscript_scope" for i in issues)


def test_exporter_skips_blank_nodes() -> None:
    graph = Graph()
    bnode = BNode()
    graph.add((bnode, RDF.type, HM.Evidence))
    graph.add((bnode, RDFS.label, Literal("BlankNode n1011", lang="en")))
    drafts = HmoWikibaseExporter().from_graph(graph)
    assert drafts == []


def test_exporter_drops_hebrew_duplicated_en_label() -> None:
    graph = Graph()
    work = URIRef(f"{HM}Work_test")
    graph.add((work, RDF.type, LRMOO.F1_Work))
    graph.add((work, RDFS.label, Literal("ספר תהילים", lang="he")))
    graph.add((work, RDFS.label, Literal("ספר תהילים", lang="en")))
    graph.add((work, RDFS.comment, Literal("Literary work 'ספר תהילים' in manuscript 990001.", lang="en")))
    drafts = HmoWikibaseExporter().from_graph(graph)
    assert len(drafts) == 1
    assert drafts[0].labels.get("he") == "ספר תהילים"
    assert "en" not in drafts[0].labels
    assert audit_entity_drafts(drafts) == []


def _draft(entity_type: str, labels: dict[str, str], **kw) -> WikibaseEntityDraft:
    return WikibaseEntityDraft(
        local_id=kw.get("local_id", "QDraft_test"),
        labels=labels,
        descriptions=kw.get("descriptions", {"en": "substantive description of the item"}),
        entity_type=entity_type,
        class_uri=kw.get("class_uri", "http://example/class"),
        source_uri=kw.get("source_uri", "http://example/item"),
        statements=[],
        control_numbers=kw.get("control_numbers", ["990001"]),
    )


def test_production_missing_label_flagged() -> None:
    bad = _draft("E12_Production", {"en": "Production 990001"})
    assert any(i.code == "production_missing_label" for i in audit_entity_draft(bad))
    good = _draft("E12_Production", {"en": "Production of MS 990001 (Rome, 1460)"})
    assert not any(i.code == "production_missing_label" for i in audit_entity_draft(good))


def test_production_description_repeats_label_flagged() -> None:
    bad = _draft(
        "E12_Production",
        {"en": "Production of MS 990001"},
        descriptions={"en": "Production event for manuscript 990001."},
    )
    codes = {i.code for i in audit_entity_draft(bad)}
    assert "production_description_repeats_label" in codes

    good = _draft(
        "E12_Production",
        {"en": "Production of MS 990001"},
        descriptions={
            "en": (
                "Production of manuscript 990001 ('ספר תהילים', shelfmark Heb. 12.34); "
                "production place and date are not recorded in the catalog record."
            )
        },
    )
    codes = {i.code for i in audit_entity_draft(good)}
    assert "production_description_repeats_label" not in codes


def test_timespan_bare_label_flagged() -> None:
    bad = _draft("E52_Time-Span", {"en": "1460"})
    assert any(i.code == "timespan_bare_label" for i in audit_entity_draft(bad))
    good = _draft("E52_Time-Span", {"en": "Production period 1460"})
    assert not any(i.code == "timespan_bare_label" for i in audit_entity_draft(good))


def test_latin_label_in_he_flagged() -> None:
    bad = _draft("E74_Group", {"he": "Sassoon, David Solomon"})
    assert any(i.code == "latin_label_in_he" for i in audit_entity_draft(bad))
    good = _draft("E21_Person", {"he": "יהודה בן יוסף"})
    assert not any(i.code == "latin_label_in_he" for i in audit_entity_draft(good))


def test_unbalanced_label_quotes_flagged() -> None:
    bad = _draft("F1_Work", {"he": 'הוא תיקוני עוונות" ו'})
    assert any(i.code == "unbalanced_label_quotes" for i in audit_entity_draft(bad))
    # Hebrew gershayim abbreviation must NOT trip the check.
    ok = _draft("E21_Person", {"he": 'שד"ל'})
    assert not any(i.code == "unbalanced_label_quotes" for i in audit_entity_draft(ok))


def test_witness_unusable_title_flagged() -> None:
    bad = _draft("TransmissionWitness", {})
    assert any(i.code == "witness_unusable_title" for i in audit_entity_draft(bad))
    good = _draft("TextTradition", {"he": "אב הרחמים"})
    assert not any(i.code == "witness_unusable_title" for i in audit_entity_draft(good))
