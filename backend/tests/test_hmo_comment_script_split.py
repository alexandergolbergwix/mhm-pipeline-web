"""Regression tests for the script-split comment stamping (Rule W-69).

Run 3494ebf5 produced 1170 ``hmo.description.language`` fails: English
descriptions embedded Hebrew titles/contents, and the exporter merged
Hebrew ``rdfs:comment`` literals into the ``en`` description.
"""

from __future__ import annotations

from rdflib import RDFS, Graph, Literal, URIRef

from converter.rdf.graph_builder import GraphBuilder, _split_scripts
from converter.wikibase.hmo_exporter import _descriptions_for_node


def test_split_scripts_moves_hebrew_out_of_english() -> None:
    en, he = _split_scripts(
        "Literary work in manuscript 990000827290205171: 'חדושים על הטור'."
    )
    assert en == "Literary work in manuscript 990000827290205171."
    assert "חדושים על הטור" in he
    assert not any("\u0590" <= ch <= "\u05ff" for ch in en)


def test_split_scripts_keeps_pure_english() -> None:
    text = "Hebrew manuscript (NLI control number 1, shelfmark Ms. Heb. 2=3)."
    en, he = _split_scripts(text)
    assert en == text
    assert he == ""


def test_split_scripts_no_text_is_lost() -> None:
    text = "Work-creation event: לוריא, שלמה authored 'חדושים'."
    en, he = _split_scripts(text)
    assert "לוריא" in he and "חדושים" in he
    assert en == "Work-creation event."


def test_stamp_wikibase_comment_splits_and_preserves_existing_he() -> None:
    graph = Graph()
    node = URIRef("https://w3id.org/mhm/ontology#MS_1")
    GraphBuilder._stamp_wikibase_comment(graph, node, "קבץ מתימן", lang="he")
    GraphBuilder._stamp_wikibase_comment(graph, node, "A work: 'תיקון עולה'.")
    en = [str(v) for v in graph.objects(node, RDFS.comment)
          if isinstance(v, Literal) and v.language == "en"]
    he = [str(v) for v in graph.objects(node, RDFS.comment)
          if isinstance(v, Literal) and v.language == "he"]
    assert en == ["A work."]
    assert "קבץ מתימן" in he[0] and "תיקון עולה" in he[0]


def test_descriptions_for_node_never_merges_scripts() -> None:
    graph = Graph()
    node = URIRef("https://w3id.org/mhm/ontology#MS_1")
    graph.add((node, RDFS.label, Literal("Codex A", lang="en")))
    graph.add((node, RDFS.comment, Literal("Hebrew manuscript (shelfmark F 1).", lang="en")))
    graph.add((node, RDFS.comment, Literal("העתק גביית עדות מבית-הדין", lang="he")))
    descriptions = _descriptions_for_node(
        graph, node, URIRef("https://w3id.org/mhm/ontology#Manuscript")
    )
    assert "Hebrew manuscript" in descriptions["en"]
    assert not any("\u0590" <= ch <= "\u05ff" for ch in descriptions["en"])
    assert "העתק גביית עדות" in descriptions.get("he", "")


def test_cu_metadata_stamps_hebrew_work_title_on_he_side() -> None:
    graph = Graph()
    cu = URIRef("https://w3id.org/mhm/ontology#CU_1_1")
    GraphBuilder._stamp_codicological_unit_metadata(
        graph, cu, control_number="990000550960205171",
        sequence=3, work_title="לוחות לשנים", shelfmark="F 40252",
    )
    en = [str(v) for v in graph.objects(cu, RDFS.comment)
          if isinstance(v, Literal) and v.language == "en"]
    he = [str(v) for v in graph.objects(cu, RDFS.comment)
          if isinstance(v, Literal) and v.language == "he"]
    assert en and "containing" not in en[0]
    assert "לוחות לשנים" in he[0]


def test_boolean_flags_become_typed_vocabulary_links() -> None:
    """26 skipped statements: booleans cannot shape a wikibase-item claim."""
    graph = Graph()
    ms = URIRef("https://w3id.org/mhm/ontology#MS_1")
    builder = GraphBuilder()
    builder._stamp_vocabulary_individual(
        graph, URIRef("https://w3id.org/mhm/ontology#Vocab_Vocalization_present"),
        URIRef("https://w3id.org/mhm/ontology#VocalizationType"), "Vocalization present",
    )
    graph.add((ms, URIRef("https://w3id.org/mhm/ontology#has_vocalization"),
               URIRef("https://w3id.org/mhm/ontology#Vocab_Vocalization_present")))
    vocal_type = graph.value(
        URIRef("https://w3id.org/mhm/ontology#Vocab_Vocalization_present"),
        URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type"),
    )
    assert vocal_type is not None
