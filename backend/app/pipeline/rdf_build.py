"""RDF Graph — RDF graph build + SHACL validation + Cytoscape JSON.

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
import re
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


def _prepare_record_for_rdf(rec: dict[str, Any]) -> dict[str, Any]:
    """Normalise a ``run_records.marc`` row before RDF mapping.

    Delegates to the shared ``prepare_record_for_pipeline`` helper in
    ``marc_ingest`` and additionally ensures ``_control_number`` is set.
    """
    from app.pipeline.marc_ingest import prepare_record_for_pipeline  # noqa: PLC0415

    row = prepare_record_for_pipeline(rec)
    cn = (
        row.get("_control_number")
        or row.get("control_number")
        or row.get("controlNumber")
        or row.get("001")
        or row.get("id")
    )
    if cn:
        row["_control_number"] = str(cn)
    return row


def _merge_ner_entities(rec: dict[str, Any], entities: list[dict[str, Any]]) -> None:
    """Merge approved NER entities into the flat MARC record dict.

    Called once per record before ``ExtractedData`` is built, so the
    approved entities flow into the RDF graph like any other MARC-derived
    field.  Three channels are supported:

    * ``person_ner``    → ``rec["authors"]`` (role=AUTHOR) or
                          ``rec["contributors"]`` (all other roles)
    * ``genre_ml``      → ``rec["genres"]``
    * ``contents_ner``  → ``rec["contents"]`` (WORK entities only)

    Deduplicates by lowercased name / title so a person already captured
    from a structured MARC field is not added a second time.
    """
    for ent in entities:
        src  = ent.get("source", "")
        text = (ent.get("text") or "").strip()
        if not text:
            continue
        ent_type = (ent.get("type") or "").upper()
        role     = (ent.get("role") or "").upper()

        if src == "person_ner":
            target_key = "authors" if role == "AUTHOR" else "contributors"
            existing: list[dict] = rec.setdefault(target_key, []) or []
            existing_names = {(a.get("name") or "").lower() for a in existing}
            if text.lower() not in existing_names:
                existing.append({"name": text, "role": role.lower() or "related"})
            rec[target_key] = existing

        elif src == "genre_ml":
            genres: list[str] = rec.setdefault("genres", []) or []
            if text not in genres:
                genres.append(text)
            rec["genres"] = genres

        elif src == "contents_ner" and ent_type == "WORK":
            contents: list[dict] = rec.setdefault("contents", []) or []
            existing_titles = {(c.get("title") or "").lower() for c in contents}
            if text.lower() not in existing_titles:
                contents.append({"title": text})
            rec["contents"] = contents


def _merge_authority_ids(rec: dict[str, Any], matches: list[dict[str, Any]]) -> None:
    """Merge approved authority identifiers from *matches* into *rec*.

    Person matches: viaf_id, birth_year, death_year, wikidata_id (and
    authority_id when mazal_id carries an NLI number) are written into
    the matching entry in rec["authors"] or rec["contributors"] by
    case-insensitive name comparison.  Only absent fields are filled so
    the curator's MARC-embedded values are never clobbered.

    KIMA place matches (payload.kima_id is set): geonames_id, lat, lon,
    wikidata_id are written into the matching entry in rec["subjects"]
    by case-insensitive term comparison.  The graph builder then uses
    these to emit OWL.sameAs + WGS84 triples for the place node.
    """
    for m in matches:
        payload = m.get("payload") or {}
        entity_text = (m.get("entity_text") or "").strip().lower()
        if not entity_text:
            continue

        is_kima = bool(payload.get("kima_id"))
        entity_kind = (m.get("entity_kind") or "person").lower()

        if is_kima or entity_kind == "place":
            kima_lat  = payload.get("kima_lat")
            kima_lon  = payload.get("kima_lon")
            kima_geo  = payload.get("kima_geonames")
            wikidata_qid = m.get("wikidata_qid")

            for subj in rec.get("subjects") or []:
                term = (subj.get("term") or "").strip().lower()
                if not term:
                    continue
                if term == entity_text or entity_text in term or term in entity_text:
                    if kima_geo and "geonames_id" not in subj:
                        subj["geonames_id"] = str(kima_geo)
                    if kima_lat is not None and "lat" not in subj:
                        subj["lat"] = kima_lat
                    if kima_lon is not None and "lon" not in subj:
                        subj["lon"] = kima_lon
                    if wikidata_qid and "wikidata_id" not in subj:
                        subj["wikidata_id"] = wikidata_qid
                    break

            # Also propagate coords to the production place (rec["place"]).
            if kima_lat is not None and kima_lon is not None:
                prod_place = str(rec.get("place") or "").strip().lower()
                if prod_place and (
                    prod_place == entity_text
                    or entity_text in prod_place
                    or prod_place in entity_text
                ):
                    if "production_place_lat" not in rec:
                        rec["production_place_lat"] = kima_lat
                    if "production_place_lon" not in rec:
                        rec["production_place_lon"] = kima_lon
                    if wikidata_qid and "production_place_wikidata_id" not in rec:
                        rec["production_place_wikidata_id"] = wikidata_qid

                # Also propagate to rec["related_places"] entries.
                for rp_name in rec.get("related_places") or []:
                    rp_lower = str(rp_name).strip().lower()
                    if not rp_lower:
                        continue
                    if (
                        rp_lower == entity_text
                        or entity_text in rp_lower
                        or rp_lower in entity_text
                    ):
                        if "related_place_coords" not in rec:
                            rec["related_place_coords"] = {}
                        existing = rec["related_place_coords"].setdefault(str(rp_name).strip(), {})
                        if "lat" not in existing:
                            existing["lat"] = kima_lat
                        if "lon" not in existing:
                            existing["lon"] = kima_lon
                        if wikidata_qid and "wikidata_id" not in existing:
                            existing["wikidata_id"] = wikidata_qid
                        break
        else:
            for target_key in ("authors", "contributors"):
                for person in rec.get(target_key) or []:
                    name = (person.get("name") or "").strip().lower()
                    if not name:
                        continue
                    if name == entity_text or entity_text in name or name in entity_text:
                        if m.get("viaf_id") and "viaf_id" not in person:
                            person["viaf_id"] = str(m["viaf_id"])
                        if payload.get("birth_year") is not None and "birth_year" not in person:
                            person["birth_year"] = payload["birth_year"]
                        if payload.get("death_year") is not None and "death_year" not in person:
                            person["death_year"] = payload["death_year"]
                        if m.get("wikidata_qid") and "wikidata_id" not in person:
                            person["wikidata_id"] = m["wikidata_qid"]
                        if m.get("mazal_id") and "authority_id" not in person:
                            person["authority_id"] = str(m["mazal_id"])
                        # Preferred names for RDF label triples
                        if payload.get("preferred_name_lat") and "preferred_name_lat" not in person:
                            person["preferred_name_lat"] = payload["preferred_name_lat"]
                        if payload.get("preferred_name_heb") and "preferred_name_heb" not in person:
                            person["preferred_name_heb"] = payload["preferred_name_heb"]
                        # Canonical URIs for owl:sameAs
                        if payload.get("viaf_uri") and "viaf_uri" not in person:
                            person["viaf_uri"] = payload["viaf_uri"]
                        # GND, LCCN, ISNI from VIAF cluster (Rule W-23)
                        cluster = payload.get("cluster_ids") or {}
                        for id_key in ("gnd", "lc", "isni", "bnf", "j9u"):
                            if cluster.get(id_key) and id_key not in person:
                                person[id_key] = cluster[id_key]
                        break


def _run_mapper_sync(
    marc_records: list[dict],
    authority_matches: list[dict],
    output_path: Path,
    entities_by_cn: dict[str, list[dict[str, Any]]] | None = None,
    overrides: list[dict] | None = None,
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

    ents_by_cn: dict[str, list[dict[str, Any]]] = entities_by_cn or {}

    mapper = MarcToRdfMapper()
    combined = Graph()

    from converter.config.namespaces import bind_namespaces  # noqa: PLC0415

    bind_namespaces(combined)

    manuscripts = 0
    errors: list[str] = []
    for raw_rec in marc_records:
        rec = _prepare_record_for_rdf(raw_rec)
        cn = str(
            rec.get("_control_number")
            or rec.get("control_number")
            or rec.get("controlNumber")
            or f"rec_{id(raw_rec)}"
        )
        # URI-safe CN: strip surrounding/embedded quotes and replace any
        # character that is not valid inside a URI fragment with "_".
        # Used only for URI construction in build_graph; the raw cn is kept
        # for authority-match lookups so cross-references are not broken.
        cn_uri = re.sub(r"[^\w.\-]", "_", cn.strip("\"'")).strip("_") or cn

        # Merge approved NER entities into the MARC record's field lists.
        # Try both the raw CN (possibly with surrounding quotes from the DB)
        # and the stripped version so the lookup is robust.
        cn_stripped = cn.strip("\"'")
        ner_ents = ents_by_cn.get(cn) or ents_by_cn.get(cn_stripped) or []
        if ner_ents:
            _merge_ner_entities(rec, ner_ents)

        rec_matches = matches_by_cn.get(cn) or matches_by_cn.get(cn_stripped) or []
        if rec_matches:
            _merge_authority_ids(rec, rec_matches)

        try:
            # Build ExtractedData from the dict — same pattern as
            # MarcToRdfMapper.map_json_records.
            extracted = ExtractedData()
            for field_name in vars(extracted):
                if field_name.startswith("_"):
                    continue
                if field_name in rec:
                    setattr(extracted, field_name, rec[field_name])
            extracted.control_number = cn_uri
            # Fold approved authority matches into the extracted bag the
            # mapper consumes (best-effort — schema mirrors the desktop
            # pipeline's authority_enriched payload).
            if cn in matches_by_cn and not getattr(
                extracted, "marc_authority_matches", None,
            ):
                extracted.marc_authority_matches = matches_by_cn[cn]  # type: ignore[attr-defined]

            graph = mapper.graph_builder.build_graph(extracted, cn_uri)
            for triple in graph:
                combined.add(triple)
            manuscripts += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"record {cn}: {exc}")
            logger.warning("RDF mapping failed for %s: %s", cn, exc)

    if overrides:
        for ov in overrides:
            subj = URIRef(ov["subject_uri"])
            pred = URIRef(ov["predicate_uri"])
            for triple in list(combined.triples((subj, pred, None))):
                combined.remove(triple)
            datatype = URIRef(ov["new_datatype"]) if ov.get("new_datatype") else None
            lang = ov.get("new_lang")
            if datatype:
                combined.add((subj, pred, Literal(ov["new_value"], datatype=datatype)))
            elif lang:
                combined.add((subj, pred, Literal(ov["new_value"], lang=lang)))
            else:
                combined.add((subj, pred, Literal(ov["new_value"])))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined.serialize(destination=str(output_path), format="turtle")
    return len(combined), manuscripts, errors


async def build_rdf_graph(
    *,
    marc_records: list[dict],
    authority_matches: list[dict],
    output_path: Path,
    entities_by_cn: dict[str, list[dict[str, Any]]] | None = None,
    overrides: list[dict] | None = None,
) -> RdfBuildResult:
    """Run ``MarcToRdfMapper`` over MARC + authority data, write Turtle.

    ``entities_by_cn`` carries approved Stage-2 NER entities keyed by
    ``control_number``.  When provided, approved persons / genres /
    work-titles are merged into each MARC record's field lists before
    ``ExtractedData`` is built, so they appear in the RDF graph alongside
    the authority-enriched data.

    Returns a structured result so the router can report counts +
    timestamps without re-parsing the TTL.
    """
    started = datetime.now(timezone.utc)
    triples_count, manuscripts_count, errors = await asyncio.to_thread(
        _run_mapper_sync, marc_records, authority_matches, output_path,
        entities_by_cn or {}, overrides,
    )
    if errors:
        logger.warning(
            "RDF build completed with %d mapping error(s) out of %d record(s)",
            len(errors),
            len(marc_records),
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


# ── Server-side layout ────────────────────────────────────────────────


# Layout names accepted by ``compute_layout``. ``preset`` means
# positions are already on the nodes; nothing to compute.
LAYOUT_KINDS = (
    "spring",        # networkx spring_layout (force-directed)
    "kamada_kawai",  # better quality, slightly slower (needs scipy)
    "circular",      # ring
    "shell",         # concentric rings by node type
    "concentric",    # alias of shell
)


def compute_layout(
    payload: dict[str, Any], *,
    kind: str = "spring",
    scale: float = 1000.0,
    seed: int = 42,
) -> dict[str, Any]:
    """Return *payload* with each node decorated with a ``position`` dict.

    The browser used to run cose-bilkent over 500 nodes which froze
    the canvas for seconds. We now ship pre-computed (x, y) positions
    and the frontend uses Cytoscape's ``preset`` layout (zero-cost
    placement). Layout takes ~1s for 500 nodes on the server.

    The output shape adds ``position: {x, y}`` to every node dict.
    Edges are unchanged.
    """
    import networkx as nx  # noqa: PLC0415 — keep import lazy

    nodes = list(payload.get("nodes") or [])
    edges = list(payload.get("edges") or [])
    if not nodes:
        return payload

    g = nx.DiGraph()
    for n in nodes:
        g.add_node(n["id"], **{"type": n.get("type", "Other")})
    for e in edges:
        if e["source"] in g and e["target"] in g:
            g.add_edge(e["source"], e["target"])

    pos: dict[str, tuple[float, float]]
    if kind == "kamada_kawai":
        try:
            pos = nx.kamada_kawai_layout(g, scale=scale)
        except Exception:  # noqa: BLE001 — scipy missing, etc.
            pos = nx.spring_layout(g, scale=scale, seed=seed, iterations=80)
    elif kind == "circular":
        pos = nx.circular_layout(g, scale=scale)
    elif kind in ("shell", "concentric"):
        # Group nodes by category so each ring is a class.
        by_class: dict[str, list[str]] = {}
        for n in nodes:
            by_class.setdefault(n.get("type", "Other"), []).append(n["id"])
        # Most-populated class in the center, smaller classes on outer rings.
        shells = sorted(by_class.values(), key=lambda v: -len(v))
        pos = nx.shell_layout(g, nlist=shells, scale=scale)
    else:  # spring (default)
        # Bound iterations so 500-node graphs finish in <1s.
        pos = nx.spring_layout(
            g, scale=scale, seed=seed, iterations=60,
            # Larger k spreads dense clusters out so the page isn't a blob.
            k=1.2 / max(1.0, (len(nodes) ** 0.5)),
        )

    positioned_nodes: list[dict[str, Any]] = []
    for n in nodes:
        xy = pos.get(n["id"])
        if xy is None:
            xy = (0.0, 0.0)
        positioned_nodes.append({
            **n,
            "position": {"x": float(xy[0]), "y": float(xy[1])},
        })
    return {"nodes": positioned_nodes, "edges": edges, "layout": kind}


# ── Per-node detail (for the click-side panel) ────────────────────────


def node_detail(graph: rdflib.Graph, node_id: str) -> dict[str, Any]:
    """Return the full detail blob for one node:

    * ``id`` + ``label`` + ``color`` + primary ``type`` (matches the
      same category the cytoscape JSON uses).
    * ``types[]`` — every ``rdf:type`` value attached to the node,
      with the URI + the local-name as label.
    * ``properties[]`` — every datatype triple (predicate + literal
      value) keyed off the node.
    * ``outgoing[]`` — every object-property triple where the node is
      the subject. Each entry: ``{predicate, predicate_label,
      target_id, target_label, target_type, target_color}``.
    * ``incoming[]`` — symmetrically, every triple where the node is
      the object.

    The frontend renders these as four sections in the side panel.
    """
    from rdflib import Literal, URIRef  # noqa: PLC0415

    subj = URIRef(node_id)
    types: list[dict[str, str]] = []
    properties: list[dict[str, Any]] = []
    outgoing: list[dict[str, Any]] = []
    incoming: list[dict[str, Any]] = []

    label = _local_name(node_id)
    primary_type = "Other"

    for s, p, o in graph.triples((subj, None, None)):
        p_uri = str(p)
        # rdf:type
        if p_uri == "http://www.w3.org/1999/02/22-rdf-syntax-ns#type":
            type_uri = str(o)
            types.append({
                "uri":   type_uri,
                "label": _local_name(type_uri),
            })
            cat = _infer_category_from_uri(type_uri)
            if cat != "Other" or primary_type == "Other":
                primary_type = cat
            continue
        # rdfs:label / hm:label / preferred name → headline label
        if p_uri.endswith("label") or p_uri.endswith("preferredName"):
            if isinstance(o, Literal):
                label = str(o)
                continue
        # Datatype property → goes in ``properties``.
        if isinstance(o, Literal):
            properties.append({
                "predicate":       p_uri,
                "predicate_label": _shorten_uri(p_uri),
                "value":           str(o),
                "datatype":        str(o.datatype) if o.datatype else None,
            })
            continue
        # Object property → outgoing edge.
        target_id = str(o)
        outgoing.append({
            "predicate":       p_uri,
            "predicate_label": _shorten_uri(p_uri),
            "target_id":       target_id,
            "target_label":    _resolve_label(graph, target_id),
            "target_type":     _resolve_category(graph, target_id),
            "target_color":    PALETTE.get(_resolve_category(graph, target_id), PALETTE["Other"]),
        })

    for s, p, _ in graph.triples((None, None, subj)):
        source_id = str(s)
        incoming.append({
            "predicate":       str(p),
            "predicate_label": _shorten_uri(str(p)),
            "source_id":       source_id,
            "source_label":    _resolve_label(graph, source_id),
            "source_type":     _resolve_category(graph, source_id),
            "source_color":    PALETTE.get(_resolve_category(graph, source_id), PALETTE["Other"]),
        })

    return {
        "id":         node_id,
        "label":      label[:120],
        "type":       primary_type,
        "color":      PALETTE.get(primary_type, PALETTE["Other"]),
        "types":      types,
        "properties": properties,
        "outgoing":   outgoing,
        "incoming":   incoming,
    }


def _resolve_label(graph: rdflib.Graph, uri: str) -> str:
    """Best-effort label lookup for a referenced node."""
    from rdflib import Literal, URIRef  # noqa: PLC0415
    n = URIRef(uri)
    for _, p, o in graph.triples((n, None, None)):
        p_uri = str(p)
        if p_uri.endswith("label") or p_uri.endswith("preferredName"):
            if isinstance(o, Literal):
                return str(o)[:80]
    return _local_name(uri)


def _resolve_category(graph: rdflib.Graph, uri: str) -> str:
    """Best-effort rdf:type-based category for a referenced node."""
    from rdflib import URIRef  # noqa: PLC0415
    n = URIRef(uri)
    for _, _, o in graph.triples(
        (n, URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type"), None),
    ):
        cat = _infer_category_from_uri(str(o))
        if cat != "Other":
            return cat
    return _infer_category_from_uri(uri)


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
    "entity_kind",
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
