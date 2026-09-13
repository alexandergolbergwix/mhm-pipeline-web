"""Keyword router + AG-UI packing for the local research agent."""
from __future__ import annotations

from app.services.research_agent.agui import last_user_text, route_user_text, sse_pack


def test_sse_pack_is_event_stream() -> None:
    packed = sse_pack({"type": "RUN_STARTED", "threadId": "t"})
    assert packed.startswith("data: {")
    assert packed.endswith("\n\n")
    assert "RUN_STARTED" in packed


def test_route_overview() -> None:
    name, args = route_user_text("Give me a corpus overview")
    assert name == "research_summary"
    assert args == {}


def test_route_wikidata_qid() -> None:
    name, args = route_user_text("Look up Wikidata Q5")
    assert name == "wikidata_entity"
    assert args["qid"] == "Q5"


def test_route_sparql() -> None:
    name, args = route_user_text("SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 5")
    assert name == "research_sparql"
    assert "SELECT" in args["query"]


def test_last_user_text_reads_content_parts() -> None:
    messages = [
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": [{"type": "text", "text": "Show the network"}]},
    ]
    assert last_user_text(messages) == "Show the network"
    name, _ = route_user_text("Show the people network")
    assert name == "research_network"
