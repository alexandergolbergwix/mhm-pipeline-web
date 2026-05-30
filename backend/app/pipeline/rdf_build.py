"""Stage 4 — RDF graph build + SHACL validation + Cytoscape JSON.

Wraps the synchronous ``MarcToRdfMapper`` and ``pyshacl.validate`` in
``asyncio.to_thread`` so they don't block the FastAPI event loop.

The Cytoscape converter is lifted from the desktop
``gui/widgets/knowledge_graph_view.py::RdfToJsonConverter``. Two
differences from the desktop version:

* The 8-class colour palette is keyed off the rdf:type local-name and
  fed straight into each node's ``color`` field (the desktop version
  ships ``bgColor`` / ``borderColor`` separately for Cytoscape.js
  styling — the web frontend wants a single colour).
* The graph is capped at ``max_nodes`` (default 500) so a 12k-triple
  manuscript graph doesn't blow up the browser.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import rdflib
from rdflib import RDF, RDFS, Graph, Literal, URIRef

logger = logging.getLogger(__name__)

# ── 8-colour palette ────────────────────────────────────────────────────
#
# Keyed off the *category* derived from the rdf:type local-name via the
# _TYPE_TO_CATEGORY map below. Same names as the desktop's
# RdfToJsonConverter so frontend code that already knows about them
# transfers 1:1.

PALETTE: dict[str, str] = {
    "Manuscript": "#5B8DEF",
    "Person": "#EF5B9C",
    "Work": "#5BEFA1",
    "Place": "#EFD25B",
    "Event": "#A15BEF",
    "Organization": "#5BCBEF",
    "Codicological": "#EF8A5B",
    "Other": "#888",
}

# Map from HMO/CIDOC/LRMoo local-names → palette category.
_TYPE_TO_CATEGORY: dict[str, str] = {
    # Manuscripts / Manifestations
    "Manuscript": "Manuscript",
    "F4_Manifestation_Singleton": "Manuscript",
    "F3_Manifestation": "Manuscript",
    # Persons
    "E21_Person": "Person",
    # Works / Expressions
    "F1_Work": "Work",
    "F24_Publication_Work": "Work",
    "F2_Expression": "Work",
    # Places
    "E53_Place": "Place",
    # Codicological / Bibliographic / Paleographical units
    "Codicological_Unit": "Codicological",
    "Bibliographic_Unit": "Codicological",
    "Paleographical_Unit": "Codicological",
    # Events
    "E12_Production": "Event",
    "E8_Acquisition": "Event",
    "E10_Transfer_of_Custody": "Event",
    "F27_Work_Creation": "Event",
    "E7_Activity": "Event",
    "CreativeEvent": "Event",
    # Organisations
    "E74_Group": "Organization",
}


@dataclass
class RdfBuildResult:
    triples_count: int
    manuscripts_count: int
    output_path: Path
    started_at: datetime
    finished_at: datetime
    mapping_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["output_path"] = str(self.output_path)
        d["started_at"] = self.started_at.isoformat()
        d["finished_at"] = self.finished_at.isoformat()
        return d


@dataclass
class ShaclViolation:
    focus_node: str
    source_shape: str
    severity: str
    message: str
    value: str | None


@dataclass
class ShaclReport:
    conforms: bool
    violations: list[ShaclViolation]

    def to_dict(self) -> dict[str, Any]:
        return {
            "conforms": self.conforms,
            "violations": [asdict(v) for v in self.violations],
        }


# ── Build ───────────────────────────────────────────────────────────────


def _local_name(uri: str) -> str:
    """Extract the local name from a URI (after # or last /)."""
    if "#" in uri:
        return uri.split("#")[-1]
    return uri.rsplit("/", 1)[-1] if "/" in uri else uri


def _shorten_uri(uri: str) -> str:
    return _local_name(uri).replace("_", " ")


def _category_for_type(local: str) -> str:
    return _TYPE_TO_CATEGORY.get(local, "Other")


def _infer_category_from_uri(uri: str) -> str:
    uri_lower = uri.lower()
    for keyword, category in (
        ("person", "Person"),
        ("ms_", "Manuscript"),
        ("manuscript", "Manuscript"),
        ("work", "Work"),
        ("expression", "Work"),
        ("place", "Place"),
        ("cu_", "Codicological"),
        ("event", "Event"),
        ("creation", "Event"),
        ("production", "Event"),
        ("group", "Organization"),
    ):
        if keyword in uri_lower:
            return category
    return "Other"


def _run_mapper_sync(
    marc_records: list[dict],
    authority_matches: list[dict],
    output_path: Path,
) -> tuple[int, int, list[str]]:
    """Synchronous core — runs in a thread. Returns
    (triples_count, manuscripts_count, mapping_errors)."""
    from converter.transformer.field_handlers import ExtractedData
    from converter.transformer.mapper import MarcToRdfMapper

    # Authority matches are keyed off control_number — fold them into
    # each MARC record under a stable key the field-handlers can pick up.
    matches_by_cn: dict[str, list[dict]] = {}
    for m in authority_matches:
        cn = str(m.get("control_number", ""))
        if not cn:
            continue
        matches_by_cn.setdefault(cn, []).append(m)

    mapper = MarcToRdfMapper()
    combined = Graph()

    from converter.config.namespaces import bind_namespaces  # noqa: PLC0415

    bind_namespaces(combined)

    manuscripts = 0
    errors: list[str] = []
    for rec in marc_records:
        cn = str(
            rec.get("_control_number")
            or rec.get("control_number")
            or rec.get("controlNumber")
            or f"rec_{id(rec)}"
        )
        try:
            # Build ExtractedData from the dict — same pattern as
            # MarcToRdfMapper.map_json_records.
            extracted = ExtractedData()
            for field_name in vars(extracted):
                if field_name.startswith("_"):
                    continue
                if field_name in rec:
                    setattr(extracted, field_name, rec[field_name])
            extracted.control_number = cn
            # Fold approved/cross-source authority matches into the
            # extracted bag the mapper consumes (best-effort — schema
            # mirrors the desktop pipeline's authority_enriched payload).
            if cn in matches_by_cn and not getattr(
                extracted, "marc_authority_matches", None,
            ):
                extracted.marc_authority_matches = matches_by_cn[cn]  # type: ignore[attr-defined]

            graph = mapper.graph_builder.build_graph(extracted, cn)
            for triple in graph:
                combined.add(triple)
            manuscripts += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"record {cn}: {exc}")
            logger.warning("RDF mapping failed for %s: %s", cn, exc)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined.serialize(destination=str(output_path), format="turtle")
    return len(combined), manuscripts, errors


async def build_rdf_graph(
    *,
    marc_records: list[dict],
    authority_matches: list[dict],
    output_path: Path,
) -> RdfBuildResult:
    """Run ``MarcToRdfMapper`` over MARC + authority data, write Turtle.

    Returns a structured result so the router can report counts +
    timestamps without re-parsing the TTL.
    """
    started = datetime.now(timezone.utc)
    triples_count, manuscripts_count, errors = await asyncio.to_thread(
        _run_mapper_sync, marc_records, authority_matches, output_path,
    )
    finished = datetime.now(timezone.utc)
    return RdfBuildResult(
        triples_count=triples_count,
        manuscripts_count=manuscripts_count,
        output_path=output_path,
        started_at=started,
        finished_at=finished,
        mapping_errors=errors,
    )


# ── SHACL validate ─────────────────────────────────────────────────────


_ONTOLOGY_DIR = Path(__file__).resolve().parents[2] / "ontology"
SHAPES_PATH = _ONTOLOGY_DIR / "shacl-shapes.ttl"


def _run_shacl_sync(
    graph_path: Path, shapes_path: Path,
) -> tuple[bool, list[ShaclViolation]]:
    import pyshacl  # noqa: PLC0415

    data_graph = Graph().parse(str(graph_path), format="turtle")
    shapes_graph = Graph().parse(str(shapes_path), format="turtle")

    conforms, results_graph, _ = pyshacl.validate(
        data_graph,
        shacl_graph=shapes_graph,
        inference="rdfs",
        abort_on_first=False,
        meta_shacl=False,
        advanced=True,
    )

    violations: list[ShaclViolation] = []
    if not conforms:
        SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
        for vr in results_graph.subjects(RDF.type, SH.ValidationResult):
            focus = results_graph.value(vr, SH.focusNode)
            shape = results_graph.value(vr, SH.sourceShape)
            severity = results_graph.value(vr, SH.resultSeverity)
            msg = results_graph.value(vr, SH.resultMessage)
            value = results_graph.value(vr, SH.value)
            violations.append(
                ShaclViolation(
                    focus_node=str(focus) if focus else "",
                    source_shape=str(shape) if shape else "",
                    severity=_local_name(str(severity)) if severity else "Violation",
                    message=str(msg) if msg else "",
                    value=str(value) if value is not None else None,
                )
            )
    return bool(conforms), violations


async def validate_with_shacl(graph_path: Path) -> ShaclReport:
    """Validate ``graph_path`` against ``backend/ontology/shacl-shapes.ttl``."""
    if not SHAPES_PATH.exists():
        raise FileNotFoundError(
            f"SHACL shapes file missing: {SHAPES_PATH}"
        )
    if not graph_path.exists():
        raise FileNotFoundError(f"Graph file missing: {graph_path}")
    conforms, violations = await asyncio.to_thread(
        _run_shacl_sync, graph_path, SHAPES_PATH,
    )
    return ShaclReport(conforms=conforms, violations=violations)


# ── Cytoscape JSON ─────────────────────────────────────────────────────


def graph_to_cytoscape_json(
    graph: rdflib.Graph,
    *,
    max_nodes: int = 500,
) -> dict[str, list[dict[str, Any]]]:
    """Convert an rdflib graph to Cytoscape.js JSON.

    Returns ``{"nodes": [...], "edges": [...]}``. Each node carries
    ``{id, label, type, color}``; each edge carries
    ``{id, source, target, predicate, predicate_label}``.

    Literals are folded onto their subject node as ``properties`` (so the
    UI can render them on click) rather than turning into nodes
    themselves — same as the desktop viewer.

    The result is truncated to ``max_nodes`` highest-degree nodes; edges
    whose endpoints both survive truncation are kept.
    """
    node_categories: dict[str, str] = {}
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
            category = _category_for_type(local)
            if category != "Other" or s_id not in node_categories:
                node_categories[s_id] = category

    # Collect candidate node IDs from (s, p, o) triples — only URI/BNode
    # endpoints, never literal objects.
    candidate_ids: set[str] = set()
    raw_edges: list[tuple[str, str, str]] = []  # (s_id, p_uri, o_id)
    for s, p, o in graph:
        if isinstance(o, Literal):
            continue
        if p == RDF.type:
            # rdf:type triples drive the colour but aren't shown as edges.
            candidate_ids.add(str(s))
            candidate_ids.add(str(o))
            continue
        s_id, o_id = str(s), str(o)
        candidate_ids.add(s_id)
        candidate_ids.add(o_id)
        raw_edges.append((s_id, str(p), o_id))

    # Truncate to top-N nodes by degree.
    if len(candidate_ids) > max_nodes:
        ranked = sorted(candidate_ids, key=lambda nid: -degree.get(nid, 0))
        kept_ids = set(ranked[:max_nodes])
    else:
        kept_ids = candidate_ids

    nodes: list[dict[str, Any]] = []
    for nid in kept_ids:
        category = node_categories.get(nid) or _infer_category_from_uri(nid)
        label = node_labels.get(nid) or _local_name(nid)
        nodes.append(
            {
                "id": nid,
                "label": label[:60],
                "type": category,
                "color": PALETTE.get(category, PALETTE["Other"]),
                "properties": node_props.get(nid, {}),
            }
        )

    edges: list[dict[str, Any]] = []
    for s_id, p_uri, o_id in raw_edges:
        if s_id not in kept_ids or o_id not in kept_ids:
            continue
        edges.append(
            {
                "id": f"e_{len(edges)}",
                "source": s_id,
                "target": o_id,
                "predicate": p_uri,
                "predicate_label": _shorten_uri(p_uri),
            }
        )

    return {"nodes": nodes, "edges": edges}


def load_graph(ttl_path: Path) -> rdflib.Graph:
    """Parse a Turtle file from disk — thin helper for the router."""
    g = Graph()
    g.parse(str(ttl_path), format="turtle")
    return g


# ── State directory ────────────────────────────────────────────────────


_STATE_ROOT = Path(__file__).resolve().parents[2] / "state" / "runs"


def rdf_output_path_for_run(run_id: str) -> Path:
    """Canonical location of the per-run RDF artefact on disk."""
    return _STATE_ROOT / run_id / "manuscripts.ttl"


_MATCH_FIELDS: tuple[str, ...] = (
    "control_number", "entity_text", "role", "matched_name",
    "mazal_id", "viaf_id", "wikidata_qid", "confidence", "source",
)


def _read(m: Any, key: str, default: Any = "") -> Any:
    """Read ``key`` off an ORM row OR a plain dict, with a default."""
    if isinstance(m, dict):
        return m.get(key, default)
    return getattr(m, key, default)


def normalise_matches(matches: Iterable[Any]) -> list[dict[str, Any]]:
    """Normalise ORM-row authority matches to plain dicts the mapper consumes."""
    out: list[dict[str, Any]] = []
    for m in matches:
        d: dict[str, Any] = {k: _read(m, k, "") for k in _MATCH_FIELDS}
        if not d["confidence"]:
            d["confidence"] = "low"
        d["payload"] = _read(m, "payload", None) or {}
        out.append(d)
    return out
