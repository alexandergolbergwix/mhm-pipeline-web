"""Current source title and author must corroborate a canonical work URI."""

import pytest

from app.pipeline.hmo_canonical import normalize_live_entity
from app.pipeline.hmo_canonical_wikidata import CanonicalStudioContext, native_items_from_hmo
from converter.transformer.uri_generator import UriGenerator


def source_context() -> CanonicalStudioContext:
    return CanonicalStudioContext(
        marc_by_cn={
            "990001": {
                "_control_number": "990001",
                "title": "ספר הדרושים",
                "authors": [{"name": "מחבר ראשון", "role": "author"}],
            },
            "990002": {
                "_control_number": "990002",
                "title": "ספר הדרושים",
                "authors": [{"name": "מחבר שני", "role": "author"}],
            },
        }
    )


@pytest.mark.parametrize("name,record_id", [("מחבר ראשון", "990001"), ("מחבר שני", "990002")])
def test_canonical_author_uri_keeps_the_matching_source(name: str, record_id: str) -> None:
    entity = normalize_live_entity(
        {
            "local_id": "QDraft_Work_by_author",
            "wikibase_id": "Q6104",
            "entity_type": "F1_Work",
            "source_uri": str(UriGenerator().work_uri("ספר הדרושים", name)),
            "labels": {"he": "ספר הדרושים"},
            "control_numbers": ["990001", "990002"],
        }
    )
    item = native_items_from_hmo([entity], context=source_context())[0]
    assert item.records == [record_id]
    assert [s.value for s in item.statements if s.property_id == "P2093"] == [name]
    assert item.work_candidate_evidence[0]["source_record_id"] == record_id


def test_uri_author_without_current_source_proof_does_not_create_a_work() -> None:
    entity = normalize_live_entity(
        {
            "local_id": "QDraft_Work_by_absent_author",
            "wikibase_id": "Q6104",
            "entity_type": "F1_Work",
            "source_uri": str(UriGenerator().work_uri("ספר הדרושים", "מחבר ללא מקור")),
            "labels": {"he": "ספר הדרושים"},
            "control_numbers": ["990001", "990002"],
        }
    )
    assert native_items_from_hmo([entity], context=source_context()) == []


@pytest.mark.parametrize("title", ["דרושים", "ספר הדרושים : " + "חיבור חשוב ומיוחד " * 12])
def test_real_graph_title_transforms_retain_the_authored_work(title: str) -> None:
    from rdflib import Graph
    from rdflib.namespace import RDFS

    from converter.rdf.graph_builder import GraphBuilder
    from converter.transformer.field_handlers import ExtractedData

    graph = Graph()
    uri = GraphBuilder()._add_work(
        graph, ExtractedData(title=title), "מחבר ראשון", control_number="990001"
    )
    label = str(next(graph.objects(uri, RDFS.label)))
    entity = normalize_live_entity(
        {
            "local_id": "Work_graph",
            "wikibase_id": "Q6104",
            "entity_type": "F1_Work",
            "source_uri": str(uri),
            "labels": {"he": label},
            "control_numbers": ["990001"],
        }
    )
    context = CanonicalStudioContext(
        marc_by_cn={
            "990001": {
                "_control_number": "990001",
                "title": title,
                "authors": [{"name": "מחבר ראשון", "role": "author"}],
            }
        }
    )
    items = native_items_from_hmo([entity], context=context)
    assert len(items) == 1
    assert items[0].records == ["990001"]
    assert [s.value for s in items[0].statements if s.property_id == "P2093"] == ["מחבר ראשון"]


def test_authorless_title_with_by_is_not_an_author_qualified_uri() -> None:
    from app.pipeline.hmo_canonical_wikidata import _corroborated_work_uri_sources

    title = "Song by night"
    entity = normalize_live_entity(
        {
            "local_id": "Work_song",
            "wikibase_id": "Q6104",
            "entity_type": "F1_Work",
            "source_uri": str(UriGenerator().work_uri(title)),
            "labels": {"en": title},
            "control_numbers": ["990001"],
        }
    )
    assert _corroborated_work_uri_sources(entity, source_context()) is None
