"""Graph catalog + SQLite index + viewport queries for scalable RDF visualization.

Built during ``POST /rdf/build`` and read by ``GET /rdf/catalog`` and
``GET /rdf/viewport``. Separates full-corpus filter metadata from the
bounded Cytoscape payload the browser can render.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import rdflib
from rdflib import RDF, RDFS, Graph, Literal

from app.pipeline.rdf_build import (
    LAYOUT_KINDS,
    PALETTE,
    _category_for_type,
    _infer_category_from_uri,
    _local_name,
    _shorten_uri,
    compute_layout,
)

logger = logging.getLogger(__name__)

_INDEX_BUILD_LOCKS: dict[str, threading.Lock] = {}
_INDEX_LOCKS_GUARD = threading.Lock()


def _index_build_lock(run_dir: Path) -> threading.Lock:
    key = str(run_dir.resolve())
    with _INDEX_LOCKS_GUARD:
        lock = _INDEX_BUILD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _INDEX_BUILD_LOCKS[key] = lock
        return lock


def _index_is_fresh(ttl_path: Path, run_dir: Path) -> bool:
    catalog_path = run_dir / "graph_catalog.json"
    index_path = run_dir / "graph_index.sqlite"
    if not catalog_path.exists() or not index_path.exists():
        return False
    ttl_mtime = ttl_path.stat().st_mtime
    return (
        catalog_path.stat().st_mtime >= ttl_mtime
        and index_path.stat().st_mtime >= ttl_mtime
    )

_MANUSCRIPT_TYPE_LOCALS = frozenset({
    "Manuscript",
    "F4_Manifestation_Singleton",
    "F3_Manifestation",
})

_MS_URI_RE = re.compile(r"(^|/)MS[_/]", re.IGNORECASE)


@dataclass
class ViewportParams:
    """Filter + budget for a single canvas payload."""

    types: list[str] = field(default_factory=list)
    predicates: list[str] = field(default_factory=list)
    q: str = ""
    seed: str = ""
    radius: int = 0
    max_nodes: int = 500
    layout: str = "spring"
    manuscripts_only: bool = False

    def cache_key(self) -> str:
        raw = json.dumps({
            "types": sorted(self.types),
            "predicates": sorted(self.predicates),
            "q": self.q.strip().lower(),
            "seed": self.seed,
            "radius": self.radius,
            "max_nodes": self.max_nodes,
            "layout": self.layout,
            "manuscripts_only": self.manuscripts_only,
        }, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class GraphCatalog:
    total_nodes: int
    total_edges: int
    node_types: dict[str, int]
    edge_predicates: dict[str, int]
    manuscript_ids: list[str]
    manuscript_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "node_types": self.node_types,
            "edge_predicates": self.edge_predicates,
            "manuscript_ids": self.manuscript_ids,
            "manuscript_count": self.manuscript_count,
        }


def _is_manuscript(category: str, node_id: str, type_locals: set[str]) -> bool:
    if category == "Manuscript":
        return True
    if type_locals & _MANUSCRIPT_TYPE_LOCALS:
        return True
    return bool(_MS_URI_RE.search(node_id))


def scan_graph(graph: Graph) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Scan an rdflib graph into node and edge records for the index."""
    node_categories: dict[str, str] = {}
    node_type_locals: dict[str, set[str]] = {}
    node_labels: dict[str, str] = {}
    node_props: dict[str, dict[str, list[str]]] = {}
    degree: dict[str, int] = {}

    for s, p, o in graph:
        s_id = str(s)
        degree[s_id] = degree.get(s_id, 0) + 1

        if isinstance(o, Literal):
            node_props.setdefault(s_id, {}).setdefault(
                _shorten_uri(str(p)), []
            ).append(str(o))
            if p == RDFS.label:
                node_labels[s_id] = str(o)
            continue

        o_id = str(o)
        degree[o_id] = degree.get(o_id, 0) + 1

        if p == RDF.type:
            local = _local_name(str(o))
            node_type_locals.setdefault(s_id, set()).add(local)
            category = _category_for_type(local)
            if category != "Other" or s_id not in node_categories:
                node_categories[s_id] = category

    candidate_ids: set[str] = set()
    raw_edges: list[tuple[str, str, str]] = []
    for s, p, o in graph:
        if isinstance(o, Literal):
            continue
        if p == RDF.type:
            candidate_ids.add(str(s))
            candidate_ids.add(str(o))
            continue
        s_id, o_id = str(s), str(o)
        candidate_ids.add(s_id)
        candidate_ids.add(o_id)
        raw_edges.append((s_id, str(p), o_id))

    nodes: list[dict[str, Any]] = []
    for nid in candidate_ids:
        category = node_categories.get(nid) or _infer_category_from_uri(nid)
        label = node_labels.get(nid) or _local_name(nid)
        props = node_props.get(nid, {})
        flat_props = [label, category]
        for vals in props.values():
            flat_props.extend(vals)
        haystack = " ".join(flat_props).lower()
        type_locals = node_type_locals.get(nid, set())
        is_ms = _is_manuscript(category, nid, type_locals)
        nodes.append({
            "id": nid,
            "label": label[:120],
            "type": category,
            "color": PALETTE.get(category, PALETTE["Other"]),
            "properties": props,
            "degree": degree.get(nid, 0),
            "is_manuscript": is_ms,
            "haystack": haystack,
        })

    edges: list[dict[str, Any]] = []
    for idx, (s_id, p_uri, o_id) in enumerate(raw_edges):
        edges.append({
            "id": f"e_{idx}",
            "source": s_id,
            "target": o_id,
            "predicate": p_uri,
            "predicate_label": _shorten_uri(p_uri),
        })

    return nodes, edges


def build_catalog(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> GraphCatalog:
    node_types: dict[str, int] = {}
    for n in nodes:
        t = str(n["type"])
        node_types[t] = node_types.get(t, 0) + 1

    edge_predicates: dict[str, int] = {}
    for e in edges:
        lbl = str(e.get("predicate_label") or e.get("predicate") or "")
        if not lbl:
            continue
        edge_predicates[lbl] = edge_predicates.get(lbl, 0) + 1

    manuscript_ids = [str(n["id"]) for n in nodes if n.get("is_manuscript")]

    return GraphCatalog(
        total_nodes=len(nodes),
        total_edges=len(edges),
        node_types=node_types,
        edge_predicates=edge_predicates,
        manuscript_ids=manuscript_ids,
        manuscript_count=len(manuscript_ids),
    )


def build_and_persist_index(graph: Graph, run_dir: Path) -> GraphCatalog:
    """Scan graph, write SQLite index + catalog JSON. Returns catalog."""
    nodes, edges = scan_graph(graph)
    catalog = build_catalog(nodes, edges)

    run_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = run_dir / "graph_catalog.json"
    catalog_path.write_text(
        json.dumps(catalog.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    index_path = run_dir / "graph_index.sqlite"
    _write_sqlite(index_path, nodes, edges)
    return catalog


def _write_sqlite(path: Path, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    conn = sqlite3.connect(str(tmp_path))
    try:
        conn.executescript("""
            CREATE TABLE nodes (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                label TEXT NOT NULL,
                color TEXT NOT NULL,
                degree INTEGER NOT NULL,
                is_manuscript INTEGER NOT NULL,
                haystack TEXT NOT NULL,
                properties_json TEXT NOT NULL
            );
            CREATE TABLE edges (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                predicate TEXT NOT NULL,
                predicate_label TEXT NOT NULL
            );
            CREATE INDEX idx_edges_source ON edges(source);
            CREATE INDEX idx_edges_target ON edges(target);
            CREATE INDEX idx_nodes_type ON nodes(type);
            CREATE INDEX idx_nodes_ms ON nodes(is_manuscript);
        """)
        conn.executemany(
            "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    n["id"],
                    n["type"],
                    n["label"],
                    n["color"],
                    int(n["degree"]),
                    1 if n.get("is_manuscript") else 0,
                    n["haystack"],
                    json.dumps(n.get("properties") or {}, ensure_ascii=False),
                )
                for n in nodes
            ],
        )
        conn.executemany(
            "INSERT INTO edges VALUES (?,?,?,?,?)",
            [
                (
                    e["id"],
                    e["source"],
                    e["target"],
                    e["predicate"],
                    e["predicate_label"],
                )
                for e in edges
            ],
        )
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp_path, path)


def load_catalog(run_dir: Path) -> GraphCatalog | None:
    catalog_path = run_dir / "graph_catalog.json"
    if not catalog_path.exists():
        return None
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    return GraphCatalog(
        total_nodes=int(data["total_nodes"]),
        total_edges=int(data["total_edges"]),
        node_types=dict(data.get("node_types") or {}),
        edge_predicates=dict(data.get("edge_predicates") or {}),
        manuscript_ids=list(data.get("manuscript_ids") or []),
        manuscript_count=int(data.get("manuscript_count") or 0),
    )


def ensure_index(ttl_path: Path, run_dir: Path) -> GraphCatalog:
    """Load catalog from disk or rebuild index from TTL if missing/stale."""
    if _index_is_fresh(ttl_path, run_dir):
        loaded = load_catalog(run_dir)
        if loaded is not None:
            return loaded

    with _index_build_lock(run_dir):
        if _index_is_fresh(ttl_path, run_dir):
            loaded = load_catalog(run_dir)
            if loaded is not None:
                return loaded

        graph = Graph()
        graph.parse(str(ttl_path), format="turtle")
        return build_and_persist_index(graph, run_dir)


class GraphIndexStore:
    """Read-only SQLite accessor for viewport queries."""

    def __init__(self, index_path: Path) -> None:
        self._path = index_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn

    def all_nodes(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM nodes").fetchall()
            return [self._row_to_node(r) for r in rows]
        finally:
            conn.close()

    def all_edges(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM edges").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "label": row["label"],
            "type": row["type"],
            "color": row["color"],
            "degree": row["degree"],
            "is_manuscript": bool(row["is_manuscript"]),
            "haystack": row["haystack"],
            "properties": json.loads(row["properties_json"]),
        }


def _select_viewport_nodes(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    params: ViewportParams,
) -> set[str]:
    """Choose node ids for the canvas under filter + budget rules."""
    max_nodes = max(50, min(params.max_nodes, 2000))
    by_id = {n["id"]: n for n in nodes}
    adj: dict[str, list[str]] = {}
    for e in edges:
        adj.setdefault(e["source"], []).append(e["target"])
        adj.setdefault(e["target"], []).append(e["source"])

    manuscript_ids = {n["id"] for n in nodes if n.get("is_manuscript")}

    # Ego network from seed
    if params.seed and params.seed in by_id:
        kept: set[str] = {params.seed}
        if params.radius > 0:
            frontier = deque([(params.seed, 0)])
            while frontier:
                nid, depth = frontier.popleft()
                if depth >= params.radius:
                    continue
                for nb in adj.get(nid, []):
                    if nb not in kept:
                        kept.add(nb)
                        frontier.append((nb, depth + 1))
        return _trim_to_budget(kept, by_id, manuscript_ids, max_nodes)

    # Type / predicate / search filters
    type_filter = set(params.types)
    pred_filter = set(params.predicates)
    needle = params.q.strip().lower()

    if params.manuscripts_only:
        type_filter = {"Manuscript"}

    candidate: set[str] = set(by_id)

    if type_filter:
        candidate = {nid for nid in candidate if by_id[nid]["type"] in type_filter}

    if needle:
        search_hits = {nid for nid in candidate if needle in by_id[nid]["haystack"]}
        expanded = set(search_hits)
        for nid in search_hits:
            for nb in adj.get(nid, []):
                if nb in candidate:
                    expanded.add(nb)
        candidate = expanded

    if pred_filter:
        pred_nodes: set[str] = set()
        for e in edges:
            lbl = e.get("predicate_label") or ""
            if lbl not in pred_filter:
                continue
            if e["source"] in candidate and e["target"] in candidate:
                pred_nodes.add(e["source"])
                pred_nodes.add(e["target"])
        candidate = pred_nodes

    # Default view: always keep every manuscript, then fill by degree
    if not type_filter and not pred_filter and not needle and not params.manuscripts_only:
        return _trim_to_budget(
            set(by_id.keys()), by_id, manuscript_ids, max_nodes, fill_all_ms=True,
        )

    return _trim_to_budget(candidate, by_id, manuscript_ids, max_nodes, fill_all_ms=bool(type_filter == {"Manuscript"}))


def _trim_to_budget(
    candidate: set[str],
    by_id: dict[str, dict[str, Any]],
    manuscript_ids: set[str],
    max_nodes: int,
    *,
    fill_all_ms: bool = False,
) -> set[str]:
    """Keep all manuscripts when fill_all_ms; rank the rest by degree."""
    ms_in = candidate & manuscript_ids
    if fill_all_ms:
        ms_in = manuscript_ids & set(by_id)

    kept = set(ms_in)
    remaining = max_nodes - len(kept)
    if remaining <= 0:
        return kept

    non_ms = [nid for nid in candidate if nid not in kept]
    non_ms.sort(key=lambda nid: -int(by_id[nid].get("degree", 0)))
    for nid in non_ms[:remaining]:
        kept.add(nid)
    return kept


def build_viewport_payload(
    run_dir: Path,
    params: ViewportParams,
) -> dict[str, Any]:
    """Load index and return Cytoscape JSON + truncation metadata."""
    index_path = run_dir / "graph_index.sqlite"
    if not index_path.exists():
        raise FileNotFoundError(f"Graph index missing: {index_path}")

    catalog = load_catalog(run_dir)
    store = GraphIndexStore(index_path)
    nodes = store.all_nodes()
    edges = store.all_edges()

    kept_ids = _select_viewport_nodes(nodes, edges, params)
    viewport_nodes = [n for n in nodes if n["id"] in kept_ids]
    viewport_edges = [
        e for e in edges
        if e["source"] in kept_ids and e["target"] in kept_ids
    ]

    # Predicate filter on edges when types/search narrowed nodes but not preds
    if params.predicates:
        pred_filter = set(params.predicates)
        viewport_edges = [
            e for e in viewport_edges
            if (e.get("predicate_label") or "") in pred_filter
        ]

    payload: dict[str, Any] = {
        "nodes": [
            {
                "id": n["id"],
                "label": n["label"],
                "type": n["type"],
                "color": n["color"],
                "properties": n.get("properties") or {},
            }
            for n in viewport_nodes
        ],
        "edges": viewport_edges,
    }

    layout = params.layout if params.layout in LAYOUT_KINDS else "spring"
    if len(payload["nodes"]) > 1500:
        layout = "preset"
    elif layout != "preset":
        payload = compute_layout(payload, kind=layout)

    total_nodes = catalog.total_nodes if catalog else len(nodes)
    total_edges = catalog.total_edges if catalog else len(edges)
    ms_total = catalog.manuscript_count if catalog else sum(1 for n in nodes if n.get("is_manuscript"))
    ms_in_view = sum(1 for n in viewport_nodes if n.get("is_manuscript"))

    return {
        **payload,
        "truncated": len(viewport_nodes) < total_nodes,
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "manuscript_count": ms_total,
        "manuscripts_in_view": ms_in_view,
        "layout": layout,
    }


def viewport_cache_path(run_dir: Path, params: ViewportParams) -> Path:
    return run_dir / f"graph_viewport_{params.cache_key()}_{params.layout}.json"
