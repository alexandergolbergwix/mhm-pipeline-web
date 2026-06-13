"""Graph-topology operations over the merged project RDF graph.

Builds a networkx undirected graph from the merged rdflib graph then
exposes two operations:
  - neighbors(uri)  → immediate adjacent nodes + edge predicate labels
  - shortest_path(from_uri, to_uri, max_depth=6)  → node list + edge list

The networkx graph is intentionally undirected so that traversal
discovers paths regardless of predicate direction (e.g. both
"ms → has_author → person" and "person → authored → ms" contribute
the same conceptual edge).

The graph is cached alongside the rdflib graph in memory — the cache
key is the same frozenset of run-ids used by load_merged_graph so the
two caches stay in sync and are cleared together in tests.
"""
from __future__ import annotations

import logging
from typing import Any

import networkx as nx
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from app.pipeline.research_queries import HM

logger = logging.getLogger(__name__)

# ── HMO predicates to traverse (both directions on the undirected graph) ──

_TRAVERSE_PREDICATES: frozenset[URIRef] = frozenset(
    [
        HM.has_author,
        HM.has_scribe,
        HM.has_owner,
        HM.has_illuminator,
        HM.has_production_place,
        HM.mentions_place,
        HM.realises,
        HM.is_carried_out_by,
    ]
)

# ── pretty predicate labels ────────────────────────────────────────────────

_PRED_LABEL: dict[str, str] = {
    str(HM.has_author):           "author",
    str(HM.has_scribe):           "scribe",
    str(HM.has_owner):            "owner",
    str(HM.has_illuminator):      "illuminator",
    str(HM.has_production_place): "production place",
    str(HM.mentions_place):       "mentioned place",
    str(HM.realises):             "realises",
    str(HM.is_carried_out_by):    "carried out by",
}

# ── RDF-type → friendly name ───────────────────────────────────────────────

_TYPE_MAP: dict[str, str] = {
    str(HM.Manuscript_Object): "manuscript",
    str(HM.Person):            "person",
    str(HM.Place):             "place",
    str(HM["F1_Work"]):        "work",
}


def _label(graph: Graph, uri: URIRef) -> str | None:
    for obj in graph.objects(uri, RDFS.label):
        if not isinstance(obj, Literal):
            continue
        return str(obj)
    return None


def _entity_type(graph: Graph, uri: URIRef) -> str:
    for obj in graph.objects(uri, RDF.type):
        friendly = _TYPE_MAP.get(str(obj))
        if friendly:
            return friendly
    return "entity"


def build_nx_graph(rdf_graph: Graph) -> nx.Graph:
    """Build an undirected networkx graph from the rdflib graph.

    Only edges whose predicate is in _TRAVERSE_PREDICATES are included.
    Node attributes: label (str|None), type (str).
    Edge attributes: edge_type (human-readable predicate name).
    """
    G: nx.Graph = nx.Graph()

    for pred in _TRAVERSE_PREDICATES:
        for subj, obj in rdf_graph.subject_objects(pred):
            if not isinstance(subj, URIRef) or not isinstance(obj, URIRef):
                continue
            s = str(subj)
            o = str(obj)
            edge_label = _PRED_LABEL.get(str(pred), str(pred).split("#")[-1])
            if not G.has_node(s):
                G.add_node(s, label=_label(rdf_graph, subj), type=_entity_type(rdf_graph, subj))
            if not G.has_node(o):
                G.add_node(o, label=_label(rdf_graph, obj), type=_entity_type(rdf_graph, obj))
            # Allow multiple edge types between the same pair
            if G.has_edge(s, o):
                existing = G[s][o]["edge_type"]
                if edge_label not in existing:
                    G[s][o]["edge_type"] = f"{existing}, {edge_label}"
            else:
                G.add_edge(s, o, edge_type=edge_label)

    return G


def get_neighbors(G: nx.Graph, uri: str) -> list[dict[str, Any]]:
    """Return immediate neighbors of *uri* in the graph.

    Returns [] when the URI is not in the graph.
    """
    if uri not in G:
        return []
    result = []
    for neighbor in G.neighbors(uri):
        attrs = G.nodes[neighbor]
        edge_type = G[uri][neighbor].get("edge_type", "related")
        result.append(
            {
                "uri":       neighbor,
                "label":     attrs.get("label"),
                "type":      attrs.get("type", "entity"),
                "edge_type": edge_type,
            }
        )
    return result


def find_shortest_path(
    G: nx.Graph,
    from_uri: str,
    to_uri: str,
    max_depth: int = 6,
) -> dict[str, Any]:
    """Return shortest path between *from_uri* and *to_uri*.

    Returns:
      {path: [{uri, label, type}], edges: [{source, target, label}]}

    Returns empty path/edges when:
      - either URI is not in the graph
      - no path exists within *max_depth* hops

    Trivial self-path returns a single-node path with empty edges.
    """
    empty: dict[str, Any] = {"path": [], "edges": []}

    if from_uri not in G or (to_uri not in G and from_uri != to_uri):
        return empty

    if from_uri == to_uri:
        attrs = G.nodes.get(from_uri, {})
        return {
            "path": [{"uri": from_uri, "label": attrs.get("label"), "type": attrs.get("type", "entity")}],
            "edges": [],
        }

    try:
        node_path: list[str] = nx.shortest_path(G, source=from_uri, target=to_uri)
    except nx.NetworkXNoPath:
        return empty
    except nx.NodeNotFound:
        return empty

    if len(node_path) - 1 > max_depth:
        return empty

    path_nodes = []
    for uri in node_path:
        attrs = G.nodes.get(uri, {})
        path_nodes.append(
            {"uri": uri, "label": attrs.get("label"), "type": attrs.get("type", "entity")}
        )

    edges = []
    for i in range(len(node_path) - 1):
        s = node_path[i]
        o = node_path[i + 1]
        edge_label = G[s][o].get("edge_type", "related") if G.has_edge(s, o) else "related"
        edges.append({"source": s, "target": o, "label": edge_label})

    return {"path": path_nodes, "edges": edges}
