"""Tests for the HMO ontology schema parser (Phase 2 of HMO Wikibase
Studio — see dev-docs/hmo-wikibase-studio-plan.md).

Uses tolerant lower-bound thresholds against the real ontology so
ontology growth over time doesn't rot this test; the point is pinning
"the reader walks the whole ontology" not an exact count.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Graph, Literal, Namespace
from rdflib.namespace import OWL, RDF, RDFS, XSD

from converter.wikibase.ontology_schema_reader import (
    EXTERNAL_VOCAB_PROPERTIES,
    default_hmo_ontology_path,
    read_hmo_schema,
    schema_entry_metadata_by_uri,
)

_EX = Namespace("http://example.org/hmo-test#")


def test_default_ontology_path_points_at_bundled_ttl() -> None:
    path = default_hmo_ontology_path()
    assert path.name == "hebrew-manuscripts.ttl"
    assert path.is_file()


def test_read_hmo_schema_walks_the_full_real_ontology() -> None:
    schema = read_hmo_schema()

    # Tolerant lower bounds: current real counts are 103 classes / 277
    # properties (171 object + ~107 datatype) / 0 skipped annotation
    # properties. Grow-only thresholds so ontology additions don't break CI.
    assert len(schema.classes) >= 90
    assert len(schema.properties) >= 200

    for entry in [*schema.classes, *schema.properties]:
        assert entry.label.strip() != ""
        assert entry.description.strip() != ""
        assert entry.uri.startswith("http")

    for prop in schema.properties:
        assert prop.datatype is not None
        assert prop.datatype != ""


def test_default_ontology_carries_external_vocab_properties_used_by_instance_graph() -> None:
    """RDFS/OWL/geo/CIDOC predicates the RDF instance graph emits directly
    (rdfs:comment, rdfs:seeAlso, owl:sameAs, geo:lat/long,
    cidoc:P4_has_time_span) must resolve here too, or Phase 4's item build
    (hmo_item_build.py) fails closed with UnmappedOntologyUriError for
    every run — see the 2026-07-04 "why did every AI verdict fail"/"Build
    items" incident.
    """
    schema = read_hmo_schema()
    property_uris = {p.uri for p in schema.properties}
    for entry in EXTERNAL_VOCAB_PROPERTIES:
        assert entry.uri in property_uris


def test_custom_ttl_path_does_not_get_external_vocab_properties(tmp_path: Path) -> None:
    """A synthetic fixture ontology (as used by every other test in this
    file) must stay a literal, unaugmented parse — only the bundled
    default ontology gets the external-vocab top-up."""
    graph = Graph()
    subject = _EX.Bare_Class
    graph.add((subject, RDF.type, OWL.Class))
    ttl_path = tmp_path / "test.ttl"
    graph.serialize(destination=ttl_path, format="turtle")

    schema = read_hmo_schema(ttl_path)

    assert schema.properties == []


def test_class_picks_english_label_and_keeps_other_languages_as_aliases(
    tmp_path: Path,
) -> None:
    graph = Graph()
    subject = _EX.TestClass
    graph.add((subject, RDF.type, OWL.Class))
    graph.add((subject, RDFS.label, Literal("Test Class", lang="en")))
    graph.add((subject, RDFS.label, Literal("מחלקת בדיקה", lang="he")))
    graph.add((subject, RDFS.comment, Literal("A class for testing.", lang="en")))
    ttl_path = tmp_path / "test.ttl"
    graph.serialize(destination=ttl_path, format="turtle")

    schema = read_hmo_schema(ttl_path)

    assert len(schema.classes) == 1
    entry = schema.classes[0]
    assert entry.label == "Test Class"
    assert entry.aliases == ["מחלקת בדיקה"]
    assert entry.description == "A class for testing."


def test_class_without_label_or_comment_falls_back_to_local_name(
    tmp_path: Path,
) -> None:
    graph = Graph()
    subject = _EX.Bare_Class
    graph.add((subject, RDF.type, OWL.Class))
    ttl_path = tmp_path / "test.ttl"
    graph.serialize(destination=ttl_path, format="turtle")

    schema = read_hmo_schema(ttl_path)

    assert len(schema.classes) == 1
    entry = schema.classes[0]
    assert entry.label == "Bare Class"
    assert entry.description == "HMO class: Bare_Class"


@pytest.mark.parametrize(
    ("range_uri", "expected_datatype"),
    [
        (XSD.string, "string"),
        (XSD.integer, "quantity"),
        (XSD.decimal, "quantity"),
        (XSD.boolean, "boolean"),
        (XSD.anyURI, "url"),
        (XSD.date, "time"),
        (XSD.dateTime, "time"),
    ],
)
def test_datatype_property_range_maps_to_wikibase_datatype(
    tmp_path: Path, range_uri: object, expected_datatype: str
) -> None:
    graph = Graph()
    subject = _EX.test_prop
    graph.add((subject, RDF.type, OWL.DatatypeProperty))
    graph.add((subject, RDFS.range, range_uri))
    graph.add((subject, RDFS.label, Literal("test prop", lang="en")))
    ttl_path = tmp_path / "test.ttl"
    graph.serialize(destination=ttl_path, format="turtle")

    schema = read_hmo_schema(ttl_path)

    assert len(schema.properties) == 1
    assert schema.properties[0].datatype == expected_datatype


def test_object_property_range_maps_to_wikibase_item() -> None:
    graph = Graph()
    prop = _EX.knows
    target_class = _EX.Person
    graph.add((prop, RDF.type, OWL.ObjectProperty))
    graph.add((prop, RDFS.range, target_class))
    graph.add((prop, RDFS.label, Literal("knows", lang="en")))
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".ttl", delete=False) as handle:
        ttl_path = Path(handle.name)
    graph.serialize(destination=ttl_path, format="turtle")

    schema = read_hmo_schema(ttl_path)
    ttl_path.unlink()

    assert len(schema.properties) == 1
    assert schema.properties[0].datatype == "wikibase-item"


def test_annotation_properties_are_skipped_not_dropped_silently(
    tmp_path: Path,
) -> None:
    graph = Graph()
    subject = _EX.note
    graph.add((subject, RDF.type, OWL.AnnotationProperty))
    ttl_path = tmp_path / "test.ttl"
    graph.serialize(destination=ttl_path, format="turtle")

    schema = read_hmo_schema(ttl_path)

    assert schema.properties == []
    assert schema.classes == []
    assert len(schema.skipped) == 1
    assert schema.skipped[0].kind == "AnnotationProperty"
    assert str(subject) == schema.skipped[0].uri


def test_parent_uri_from_sub_class_of_and_sub_property_of(tmp_path: Path) -> None:
    graph = Graph()
    child_class = _EX.Child
    parent_class = _EX.Parent
    graph.add((child_class, RDF.type, OWL.Class))
    graph.add((child_class, RDFS.subClassOf, parent_class))

    child_prop = _EX.child_prop
    parent_prop = _EX.parent_prop
    graph.add((child_prop, RDF.type, OWL.ObjectProperty))
    graph.add((child_prop, RDFS.subPropertyOf, parent_prop))

    ttl_path = tmp_path / "test.ttl"
    graph.serialize(destination=ttl_path, format="turtle")

    schema = read_hmo_schema(ttl_path)

    assert schema.classes[0].parent_uri == str(parent_class)
    assert schema.properties[0].parent_uri == str(parent_prop)


def test_external_identifier_properties_map_to_external_id_datatype(
    tmp_path: Path,
) -> None:
    graph = Graph()
    subject = _EX.external_identifier_nli
    graph.add((subject, RDF.type, OWL.DatatypeProperty))
    graph.add((subject, RDFS.range, XSD.string))
    graph.add((subject, RDFS.label, Literal("NLI identifier", lang="en")))
    ttl_path = tmp_path / "test.ttl"
    graph.serialize(destination=ttl_path, format="turtle")

    schema = read_hmo_schema(ttl_path)

    assert schema.properties[0].datatype == "external-id"


def test_year_boundary_properties_on_integer_range_map_to_time(
    tmp_path: Path,
) -> None:
    graph = Graph()
    subject = _EX.earliest_possible_date
    graph.add((subject, RDF.type, OWL.DatatypeProperty))
    graph.add((subject, RDFS.range, XSD.integer))
    graph.add((subject, RDFS.label, Literal("earliest possible date", lang="en")))
    ttl_path = tmp_path / "test.ttl"
    graph.serialize(destination=ttl_path, format="turtle")

    schema = read_hmo_schema(ttl_path)

    assert schema.properties[0].datatype == "time"


def test_hmo_source_uri_maps_to_url_datatype() -> None:
    schema = read_hmo_schema()
    prop = next(p for p in schema.properties if p.local_name == "hmo_source_uri")
    assert prop.datatype == "url"


def test_book_name_maps_to_monolingualtext_datatype() -> None:
    schema = read_hmo_schema()
    prop = next(p for p in schema.properties if p.local_name == "book_name")
    assert prop.datatype == "monolingualtext"


def test_property_entries_carry_owl_kind_and_range() -> None:
    schema = read_hmo_schema()
    prop = next(p for p in schema.properties if p.local_name == "has_date_of_creation")
    assert prop.property_kind == "ObjectProperty"
    assert prop.range_uri is not None
    assert "E52_Time-Span" in prop.range_uri


def test_schema_entry_metadata_by_uri_includes_verify_fields() -> None:
    metadata = schema_entry_metadata_by_uri()
    uri = next(
        p.uri for p in read_hmo_schema().properties if p.local_name == "folio_number"
    )
    entry = metadata[uri]
    assert entry["property_kind"] == "DatatypeProperty"
    assert entry["range_uri"] is not None
