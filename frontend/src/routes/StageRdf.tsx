/**
 * RDF Graph — per-run page.
 *
 * Build → Visualise → Validate → Download. The Cytoscape canvas is
 * the headline; SHACL findings collapse underneath.
 *
 * Layout selectors registered at module load (see registerLayouts).
 * The graph endpoint already returns Cytoscape-compatible node/edge
 * dicts, so we just wrap them in the ``{data: …}`` shape the React
 * component expects.
 *
 * Accessibility: the Cytoscape <canvas> is opaque to screen readers
 * (WCAG 1.3.1 + 4.1.2), so a "List view" toggle renders the same
 * graph as semantic HTML headings + lists. An aria-live region
 * announces selection changes; the Legend is a real ul; SHACL
 * disclosure uses aria-expanded/aria-controls.
 */

import React, { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { type Core, type LayoutOptions, type StylesheetJsonBlock } from "cytoscape";
import CytoscapeComponent from "react-cytoscapejs";

import { Layout } from "@/components/Layout";
import { ApiError } from "@/api/client";
import { NodeDetailPanel } from "@/components/NodeDetailPanel";
import { GraphFilters } from "@/components/rdf/GraphFilters";
import {
  emptyFilterState,
  useGraphFilters,
  type GraphFilterState,
} from "@/components/rdf/useGraphFilters";
import {
  Rdf,
  type GraphEdge,
  type GraphNode,
  type GraphResponse,
  type NodeDetail,
  type RdfBuildResponse,
  type RdfCoverageResponse,
  type RdfStatus,
  type ServerLayout,
  type ShaclReport,
} from "@/api/rdf";
import { SectionExportMenu } from "@/components/export/SectionExportMenu";
import { SectionImportButton } from "@/components/import/SectionImportButton";
import {Glass, GlassPill} from "@/components/glass";


// All layouts are computed SERVER-SIDE (networkx) — Cytoscape just uses
// ``preset`` to read pre-computed positions off node data. No layout
// extensions needed in the browser.


// Server-computed layouts. The browser used to run cose-bilkent over
// 500 nodes which froze the canvas for seconds; positions now arrive
// pre-computed by networkx on the backend, and Cytoscape uses
// ``preset`` (zero-cost) placement.
const LAYOUT_NAMES: Array<{ value: ServerLayout; label: string }> = [
  { value: "spring",       label: "Force (spring)" },
  { value: "kamada_kawai", label: "Force (kamada-kawai)" },
  { value: "shell",        label: "Concentric (by class)" },
  { value: "circular",     label: "Circular" },
];


// Fixed legend palette — corresponds to the colours the backend
// assigns by ontology class.
const LEGEND: Array<{ type: string; color: string }> = [
  { type: "Manuscript",      color: "#77cce5" },
  { type: "Person",          color: "#f6c177" },
  { type: "Work",            color: "#c4a7e7" },
  { type: "Place",           color: "#9ccfd8" },
  { type: "Event",           color: "#eb6f92" },
  { type: "Organization",    color: "#f6d6c5" },
  { type: "Codicological",   color: "#a3e0bc" },
  { type: "Other",           color: "#cfd2da" },
];


// Group order used by the screen-reader-friendly list view. Keeping
// it as a fixed tuple gives stable section ordering across runs.
// Colors match the LEGEND palette so the triple view dot is consistent
// with the canvas.
const NODE_TYPE_GROUPS: Array<{ key: string; label: string; color: string }> = [
  { key: "Manuscript",    label: "Manuscripts",   color: "#77cce5" },
  { key: "Person",        label: "Persons",       color: "#f6c177" },
  { key: "Work",          label: "Works",         color: "#c4a7e7" },
  { key: "Place",         label: "Places",        color: "#9ccfd8" },
  { key: "Event",         label: "Events",        color: "#eb6f92" },
  { key: "Organization",  label: "Organizations", color: "#f6d6c5" },
  { key: "Codicological", label: "Codicological", color: "#a3e0bc" },
  { key: "Other",         label: "Other",         color: "#cfd2da" },
];


export default function StageRdf() {
  const { runId } = useParams<{ runId: string }>();

  const [status, setStatus] = useState<RdfStatus | null>(null);
  const [graph, setGraph]   = useState<GraphResponse | null>(null);
  const [shacl, setShacl]   = useState<ShaclReport | null>(null);
  const [error, setError]   = useState<string | null>(null);

  const [busy, setBusy] = useState<"build" | "validate" | "graph" | null>(null);
  const [mappingErrors, setMappingErrors] = useState<string[]>([]);
  const [coverage, setCoverage] = useState<RdfCoverageResponse | null>(null);
  const [buildOptions, setBuildOptions] = useState({
    add_epistemological_status: true,
    add_cataloging_view: true,
    add_philological_overlay: true,
  });
  // Layout is computed SERVER-SIDE (networkx) — the browser just renders
  // pre-positioned nodes. Default = spring (force-directed, plain).
  const [layout, setLayout] = useState<ServerLayout>("spring");
  const [shaclOpen, setShaclOpen] = useState(false);
  // ID of the node the user clicked on the graph. When set, the side
  // panel mounts and fetches the full RDF detail (types + properties +
  // in/out edges) for it.
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  // Canvas (Cytoscape) vs List (semantic HTML) view of the same graph.
  // The list view is the WCAG 1.3.1 / 4.1.2 alternative for the opaque
  // <canvas>; both stay mounted in state so toggling is instantaneous.
  const [viewMode, setViewMode] = useState<"canvas" | "list">("canvas");

  // — Filter state for the chip bar above the canvas. See
  //   [useGraphFilters] for the actual filter-set computation.
  const [filterState, setFilterState] = useState<GraphFilterState>(() => emptyFilterState());

  const cyRef = useRef<Core | null>(null);

  // On mount: pull status. If ``built`` (or ``validated``), also pull
  // the graph so the canvas isn't empty.
  useEffect(() => {
    let cancelled = false;
    async function bootstrap() {
      if (!runId) return;
      try {
        const st = await Rdf.status(runId);
        if (cancelled) return;
        setStatus(st);
        if (st.status === "built" || st.status === "validated") {
          await loadGraph();
        }
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.detail : String(e));
      }
    }
    void bootstrap();
    return () => { cancelled = true; };
  }, [runId]);  // eslint-disable-line react-hooks/exhaustive-deps

  async function loadGraph(layoutOverride?: ServerLayout) {
    if (!runId) return;
    setBusy("graph"); setError(null);
    try {
      setGraph(await Rdf.graph(runId, 500, layoutOverride ?? layout));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(null);
    }
  }

  // Re-fetch graph when the user picks a different server layout.
  // First call triggers a server-side networkx layout pass; subsequent
  // calls hit the on-disk cache and return in milliseconds.
  useEffect(() => {
    if (!graph) return;            // don't fetch before initial mount loaded data
    void loadGraph(layout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout]);

  async function build() {
    if (!runId) return;
    setBusy("build"); setError(null);
    try {
      const buildResult: RdfBuildResponse = await Rdf.build(runId, buildOptions);
      setMappingErrors(buildResult.mapping_errors ?? []);
      const [st, g, cov] = await Promise.all([
        Rdf.status(runId),
        Rdf.graph(runId, 500, layout),
        Rdf.coverage(runId).catch(() => null),
      ]);
      setStatus(st);
      setGraph(g);
      setCoverage(cov);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function validate() {
    if (!runId) return;
    setBusy("validate"); setError(null);
    try {
      const r = await Rdf.validate(runId);
      setShacl(r);
      setShaclOpen(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(null);
    }
  }

  // Cytoscape elements built with pre-computed positions. Using the
  // ``preset`` layout downstream means zero browser-side layout cost.
  const elements = useMemo(() => {
    if (!graph) return [];
    return [
      ...graph.nodes.map((n) => ({
        data:     n,
        position: n.position ?? undefined,
      })),
      ...graph.edges.map((e) => ({ data: e })),
    ];
  }, [graph]);

  const stylesheet = useMemo<StylesheetJsonBlock[]>(() => ([
    {
      selector: "node",
      style: {
        "background-color":        "data(color)",
        "label":                   "data(label)",
        "color":                   "#e8edf4",
        "font-size":               9,
        "text-valign":             "bottom",      // outside the dot
        "text-halign":             "center",
        "text-margin-y":           4,
        "text-wrap":               "ellipsis",
        "text-max-width":          "120px",
        "text-opacity":            1,             // always-on label
        "text-background-color":   "rgba(0,16,8,0.75)",
        "text-background-opacity": 0.75,
        "text-background-shape":   "roundrectangle",
        "text-background-padding": "2px",
        "border-width":            1,
        "border-color":            "rgba(0, 0, 0, 0.25)",
        "width":                   "32px",
        "height":                  "32px",
      },
    },
    {
      selector: "node:selected",
      style: {
        "border-width": 3,
        "border-color": "#77cce5",
      },
    },
    {
      selector: "edge",
      style: {
        "width":                   1,
        "line-color":              "rgba(183, 216, 227, 0.4)",
        "curve-style":             "bezier",
        "target-arrow-shape":      "triangle",
        "target-arrow-color":      "rgba(183, 216, 227, 0.55)",
        "arrow-scale":             0.7,
        "label":                   "",            // gated by .show-label / hover / select
        "font-size":               8,
        "color":                   "#b7d8e3",
        "text-background-color":   "#001008",
        "text-background-opacity": 0.8,
        "text-background-padding": "2px",
        "text-rotation":           "autorotate",  // read along the edge
      },
    },
    {
      selector: "edge.show-label, edge:selected, edge.hover",
      style: {
        "label": "data(predicate_label)",
      },
    },
    {
      selector: "edge:selected, edge.hover",
      style: {
        "width":      2,
        "line-color": "#77cce5",
      },
    },
    // — Dim: applied to elements filtered out of the active set. We
    //   keep them visible (15% opacity) so the user retains spatial
    //   context instead of having the graph re-layout.
    {
      selector: "node.dim",
      style: {
        "opacity":      0.15,
        "text-opacity": 0.15,
      },
    },
    {
      selector: "edge.dim",
      style: {
        "opacity": 0.10,
      },
    },
  ]), []);

  // Always ``preset`` — node positions are already in the data thanks
  // to the server-side networkx pass. Cytoscape's preset layout just
  // reads ``data.position`` and pins each node there.
  const layoutOptions = useMemo<LayoutOptions>(() => ({
    name:    "preset",
    fit:     true,
    padding: 30,
  } as unknown as LayoutOptions), []);

  // Node labels are now always-on (handled by the stylesheet). The
  // zoom handler toggles the ``show-label`` class on every edge —
  // edges stay unlabelled at low zoom (50-edge alphabet soup) and
  // pick up their predicate label at zoom > 0.6 or on hover.
  function attachCy(cy: Core) {
    cyRef.current = cy;
    const applyEdgeLabelClass = () => {
      const show = cy.zoom() > 0.6;
      cy.edges().toggleClass("show-label", show);
    };
    cy.on("zoom", applyEdgeLabelClass);
    applyEdgeLabelClass();
    cy.on("mouseover", "edge", (evt) => evt.target.addClass("hover"));
    cy.on("mouseout",  "edge", (evt) => evt.target.removeClass("hover"));
    cy.on("tap", "node", (evt) => {
      const id = evt.target.id();
      if (id) setSelectedNodeId(id);
    });
    // Click on empty canvas closes the side panel.
    cy.on("tap", (evt) => {
      if (evt.target === cy) setSelectedNodeId(null);
    });
  }

  // Re-fit on element-set changes (layout switch, build, etc.). No
  // browser-side layout pass — positions are already on the data.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || elements.length === 0) return;
    const handle = window.requestAnimationFrame(() => {
      try {
        cy.layout(layoutOptions).run();
        cy.fit(undefined, 30);
      } catch (exc) {
        console.warn("cy fit failed:", exc);
      }
    });
    return () => window.cancelAnimationFrame(handle);
  }, [elements, layoutOptions]);

  function fitToScreen() {
    cyRef.current?.fit(undefined, 30);
  }

  // Highlight the selected node + recenter on it when selection
  // changes (e.g. via NodeDetailPanel's edge-row "navigate" link).
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.$("node:selected").unselect();
    if (selectedNodeId) {
      const n = cy.getElementById(selectedNodeId);
      if (n.nonempty()) {
        n.select();
        cy.center(n);
      }
    }
  }, [selectedNodeId]);

  const statusLabel = status?.status ?? "idle";
  const violations  = shacl?.violations ?? [];
  const grouped     = useMemo(() => groupBySeverity(violations), [violations]);

  // SHACL focus_node ids match GraphNode.id 1:1, so the SHACL chip
  // can intersect the two sets directly. Empty until Validate runs.
  const shaclFocus = useMemo(
    () => new Set(violations.map((v) => v.focus_node).filter(Boolean)),
    [violations],
  );

  // Compute filtered node + edge id sets via the pure hook.
  const activeSets = useGraphFilters({
    nodes:      graph?.nodes ?? [],
    edges:      graph?.edges ?? [],
    state:      filterState,
    shaclFocus,
    selectedId: selectedNodeId,
  });

  // Apply ``.dim`` to elements that the filter set rejected. Uses
  // ``cy.batch()`` so 500 nodes don't trigger 500 re-paints (the
  // exact bug that motivated moving layout server-side).
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.nodes().forEach((n) => {
        n.toggleClass("dim", !activeSets.nodeIds.has(n.id()));
      });
      cy.edges().forEach((e) => {
        e.toggleClass("dim", !activeSets.edgeIds.has(e.id()));
      });
    });
  }, [activeSets]);

  // Build the dynamic aria-label for the canvas + the list-view
  // counts/groupings. Both views read from the same data — the list
  // view just renders it as semantic HTML so screen readers can walk
  // it heading by heading.
  const totalNodes = graph?.total_nodes ?? 0;
  const totalEdges = graph?.total_edges ?? 0;
  const canvasAriaLabel =
    `Knowledge graph visualisation with ${totalNodes} node${totalNodes === 1 ? "" : "s"} ` +
    `and ${totalEdges} edge${totalEdges === 1 ? "" : "s"}. ` +
    `Switch to list view for a screen-reader-friendly text representation.`;

  const nodesByGroup = useMemo(() => groupNodesByType(graph?.nodes ?? []), [graph?.nodes]);
  const outgoingByNode = useMemo(
    () => indexOutgoingEdges(graph?.nodes ?? [], graph?.edges ?? []),
    [graph?.nodes, graph?.edges],
  );

  const selectedNodeLabel = useMemo(() => {
    if (!selectedNodeId || !graph) return "";
    const found = graph.nodes.find((n) => n.id === selectedNodeId);
    return found?.label ?? "";
  }, [selectedNodeId, graph]);

  return (
    <Layout>
      <div className="space-y-6">
        <Glass as="section" className="p-6 space-y-2">
          <div className="kicker">
            <Link to={`/runs/${runId}/overview`} className="hover:text-ink underline">
              ← back to run
            </Link>
            {" · "}RDF Graph
          </div>
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="text-2xl font-semibold">Knowledge graph</h2>
            <StatusPill status={statusLabel} />
          </div>
          <p className="muted text-sm">
            HMO ontology · Build the per-run TTL, visualise it, then run SHACL.
          </p>
          <div className="flex flex-wrap gap-4 text-sm pt-1">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={buildOptions.add_epistemological_status}
                onChange={(e) => setBuildOptions((o) => ({
                  ...o,
                  add_epistemological_status: e.target.checked,
                }))}
              />
              Epistemological metadata
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={buildOptions.add_cataloging_view}
                onChange={(e) => setBuildOptions((o) => ({
                  ...o,
                  add_cataloging_view: e.target.checked,
                }))}
              />
              Cataloging view
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={buildOptions.add_philological_overlay}
                onChange={(e) => setBuildOptions((o) => ({
                  ...o,
                  add_philological_overlay: e.target.checked,
                }))}
              />
              Philological overlay
            </label>
          </div>
        </Glass>

        {coverage && (
          <Glass as="section" className="p-6 space-y-3" data-testid="rdf-coverage-panel">
            <h3 className="text-lg font-semibold">Ontology coverage</h3>
            <p className="muted text-sm">
              {coverage.rdf_class_count} HMO classes in graph
              {coverage.unknown_class_count > 0
                ? ` · ${coverage.unknown_class_count} unmapped`
                : " · all classes mapped"}
            </p>
            <div className="overflow-x-auto max-h-48">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left muted border-b border-white/10">
                    <th className="py-1 pr-3">Class</th>
                    <th className="py-1 pr-3">Nodes</th>
                    <th className="py-1">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {coverage.classes
                    .filter((c) => c.hmo_node_count > 0)
                    .slice(0, 20)
                    .map((c) => (
                      <tr key={c.class_uri} className="border-b border-white/5">
                        <td className="py-1 pr-3">{c.class_local_name}</td>
                        <td className="py-1 pr-3">{c.hmo_node_count}</td>
                        <td className="py-1">{c.projection_status}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </Glass>
        )}

        <Glass as="section" className="p-6 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <button onClick={build} disabled={busy !== null} className="button-primary text-sm">
                {busy === "build"
                  ? "Building…"
                  : statusLabel === "idle" ? "Build RDF" : "Re-build RDF"}
              </button>
              <button onClick={validate}
                      disabled={busy !== null || statusLabel === "idle"}
                      className="button-ghost text-sm">
                {busy === "validate" ? "Validating…" : "Validate (SHACL)"}
              </button>
              <a href={runId ? Rdf.downloadUrl(runId) : "#"}
                 className={`button-ghost text-sm ${statusLabel === "idle" ? "opacity-50 pointer-events-none" : ""}`}
                 download>
                Download .ttl
              </a>
              {runId && (
                <SectionExportMenu
                  section="rdf"
                  runId={runId}
                  availableFormats={["ttl", "nt"]}
                />
              )}
              {runId && (
                <SectionImportButton
                  section="rdf"
                  runId={runId}
                  accept=".ttl"
                  onComplete={() => {
                    if (runId) Rdf.status(runId).then(setStatus).catch(() => null);
                  }}
                />
              )}
              <span className="muted text-sm ml-2">
                Layout:&nbsp;
                <select value={layout}
                        onChange={(e) => setLayout(e.target.value as ServerLayout)}
                        className="input-glass !py-1 !w-auto text-xs inline">
                  {LAYOUT_NAMES.map((l) => (
                    <option key={l.value} value={l.value}>{l.label}</option>
                  ))}
                </select>
              </span>
              <button onClick={fitToScreen}
                      disabled={!graph || elements.length === 0 || viewMode !== "canvas"}
                      title="Recenter + zoom to fit the visible graph"
                      className="button-ghost text-sm">
                Fit
              </button>
              {/* View toggle — Canvas (Cytoscape) vs List (semantic HTML).
                  The list view is the WCAG 1.3.1 / 4.1.2 alternative for
                  the opaque <canvas>. */}
              <button
                data-testid="view-toggle-button"
                onClick={() => setViewMode((m) => (m === "canvas" ? "list" : "canvas"))}
                aria-pressed={viewMode === "list"}
                className="button-ghost text-sm"
              >
                {viewMode === "canvas" ? "List view" : "Canvas view"}
              </button>
            </div>
            {status && (
              <div className="muted text-sm">
                {status.triples_count !== undefined && status.triples_count !== null && (
                  <span>{status.triples_count.toLocaleString()} triples</span>
                )}
                {status.manuscripts_count !== undefined && status.manuscripts_count !== null && (
                  <> · {status.manuscripts_count} manuscript{status.manuscripts_count === 1 ? "" : "s"}</>
                )}
                {graph && (
                  <> · {graph.total_nodes} nodes · {graph.total_edges} edges</>
                )}
              </div>
            )}
          </div>

          {error && (
            <GlassPill as="div" className="px-3 py-2 text-sm text-red-300">{error}</GlassPill>
          )}

          {mappingErrors.length > 0 && (
            <details className="mt-2 rounded border border-amber-500/40 bg-amber-950/30 px-3 py-2 text-sm">
              <summary className="cursor-pointer font-medium text-amber-400">
                ⚠ {mappingErrors.length} record{mappingErrors.length !== 1 ? "s" : ""} failed to map to RDF — click to see errors
              </summary>
              <ul className="mt-2 space-y-1 text-amber-300/80 font-mono text-xs max-h-48 overflow-y-auto">
                {mappingErrors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </details>
          )}

          {graph && graph.nodes.length > 0 && (
            <p className="muted text-xs">
              Built from approved AI extraction + authority enrichment data
            </p>
          )}

          {/* Filter bar — always rendered once a graph is loaded. */}
          {graph && graph.nodes.length > 0 && (
            <GraphFilters
              nodes={graph.nodes}
              edges={graph.edges}
              shaclFocus={shaclFocus}
              selectedId={selectedNodeId}
              state={filterState}
              onChange={setFilterState}
              visibleCount={activeSets.nodeIds.size}
              totalCount={graph.nodes.length}
            />
          )}

          {/* aria-live status region — announces selection changes.
              Positioned off-screen so sighted users don't see it but
              screen readers still pick it up. */}
          <div
            data-testid="rdf-status-live"
            role="status"
            aria-live="polite"
            aria-atomic="true"
            style={{
              position: "absolute",
              left:     "-9999px",
              width:    "1px",
              height:   "1px",
              overflow: "hidden",
            }}
          >
            {selectedNodeLabel ? `Selected: ${selectedNodeLabel}` : ""}
          </div>

          {/* CANVAS VIEW — Cytoscape. Hidden (display: none) when the
              list view is active, but stays mounted so the cy instance
              and its layout/state survive toggling. */}
          <div
            data-testid="canvas-view"
            role="img"
            aria-label={canvasAriaLabel}
            className="relative w-full"
            style={{ height: "600px", display: viewMode === "canvas" ? "block" : "none" }}
          >
            <CytoscapeComponent
              elements={elements}
              cy={attachCy}
              layout={layoutOptions}
              stylesheet={stylesheet}
              style={{
                width:        "100%",
                height:       "100%",
                background:   "rgba(0, 16, 8, 0.4)",
                borderRadius: "20px",
                border:       "1px solid var(--line)",
              }}
              minZoom={0.1}
              maxZoom={3}
              wheelSensitivity={0.2}
            />
            <Legend />
            {graph?.truncated && (
              <GlassPill as="div" className="absolute top-3 left-3 px-3 py-1 text-[10px] kicker text-yellow-300">
                Showing {graph.nodes.length} of {graph.total_nodes} nodes (top by degree)
              </GlassPill>
            )}
            {!graph && busy !== "build" && busy !== "graph" && (
              <div className="absolute inset-0 flex items-center justify-center">
                <p className="muted">
                  {statusLabel === "idle"
                    ? "No graph yet — click Build RDF."
                    : "Click Build RDF to refresh."}
                </p>
              </div>
            )}
            {/* Loading overlay — covers the canvas while the server
                computes layout. Networkx for 500 nodes is ~1 s on
                first request; cached calls return in ~50ms. */}
            {(busy === "graph" || busy === "build") && (
              <div className="absolute inset-0 flex items-center justify-center
                              bg-black/40 backdrop-blur-sm rounded-2xl">
                <GlassPill as="div" className="px-4 py-3 text-center space-y-1.5">
                  <div className="text-sm text-ink">
                    {busy === "build" ? "Building RDF graph…" : "Computing layout on server…"}
                  </div>
                  <div className="muted text-[11px]">
                    {busy === "build"
                      ? "Running MarcToRdfMapper across the run's records."
                      : "Networkx is positioning the nodes. First request takes ~1 s; cached layouts return instantly."}
                  </div>
                  <div className="mt-2 mx-auto w-32 h-1 rounded-full overflow-hidden bg-white/10">
                    <div className="h-full w-1/3 bg-biu-sky animate-pulse" />
                  </div>
                </GlassPill>
              </div>
            )}
            {selectedNodeId && runId && (
              <NodeDetailPanel
                runId={runId}
                nodeId={selectedNodeId}
                onClose={() => setSelectedNodeId(null)}
                onNavigate={(id) => setSelectedNodeId(id)}
              />
            )}
          </div>

          {/* LIST VIEW — RDF triple table, grouped by node type. Each
              node row expands on demand to show its literal triples
              (editable) and outgoing object triples (read-only). */}
          {viewMode === "list" && runId && (
            <ListView
              testId="list-view"
              nodesByGroup={nodesByGroup}
              outgoingByNode={outgoingByNode}
              totalNodes={totalNodes}
              onSelect={setSelectedNodeId}
              selectedNodeId={selectedNodeId}
              runId={runId}
              onTripleSaved={() => { void Rdf.status(runId).then(setStatus).catch(() => null); }}
            />
          )}

        </Glass>

        <Glass as="section" className="p-6 space-y-4">
          <button
            onClick={() => setShaclOpen((v) => !v)}
            aria-expanded={shaclOpen}
            aria-controls="shacl-panel"
            className="w-full flex items-center justify-between text-left"
          >
            <div>
              <div className="kicker">SHACL validation</div>
              <h3 className="text-lg font-medium">
                {shacl
                  ? (shacl.conforms
                      ? "All shapes pass"
                      : `${violations.length} finding${violations.length === 1 ? "" : "s"}`)
                  : "Not run yet"}
              </h3>
            </div>
            <span className="muted">{shaclOpen ? "▾" : "▸"}</span>
          </button>

          {shaclOpen && (
            <div id="shacl-panel">
              {shacl == null ? (
                <p className="muted text-sm">Click Validate (SHACL) above to run.</p>
              ) : shacl.conforms ? (
                <p className="text-biu-sky text-sm">✓ Conforms to all SHACL shapes.</p>
              ) : (
                <div className="space-y-4">
                  {(["Violation", "Warning", "Info"] as const).map((sev) => {
                    const items = grouped[sev] ?? [];
                    if (items.length === 0) return null;
                    return (
                      <div key={sev}>
                        <div className="kicker mb-2">
                          {sev} ({items.length})
                        </div>
                        <ul className="space-y-2 text-sm">
                          {items.map((v, i) => (
                            <li key={`${sev}-${i}`}
                                className="border-l-2 pl-3 py-1"
                                style={{
                                  borderColor:
                                    sev === "Violation" ? "#eb6f92"
                                    : sev === "Warning" ? "#f6c177"
                                    : "#77cce5",
                                }}>
                              <div className="text-ink">{v.message}</div>
                              <div className="muted text-xs font-mono break-all">
                                {v.focus_node}
                              </div>
                              {v.value && (
                                <div className="muted text-xs">value: {v.value}</div>
                              )}
                            </li>
                          ))}
                        </ul>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </Glass>
      </div>
    </Layout>
  );
}


/**
 * Bucket the graph nodes by ontology type group. The grouping uses a
 * fixed key order (see NODE_TYPE_GROUPS) so the list view's sections
 * are stable across runs. Anything whose `type` doesn't match a known
 * key falls into "Other".
 */
function groupNodesByType(nodes: GraphNode[]): Record<string, GraphNode[]> {
  const known = new Set(NODE_TYPE_GROUPS.map((g) => g.key));
  const acc: Record<string, GraphNode[]> = {};
  for (const g of NODE_TYPE_GROUPS) {
    acc[g.key] = [];
  }
  for (const n of nodes) {
    const key = known.has(n.type) ? n.type : "Other";
    (acc[key] ||= []).push(n);
  }
  return acc;
}


/**
 * Build a Map: nodeId → outgoing edges. Used by the list view to
 * render a short "predicate: target-label" summary next to each
 * node. We pre-index in one pass so the per-node render is O(1).
 */
function indexOutgoingEdges(
  nodes: GraphNode[],
  edges: GraphEdge[],
): Map<string, Array<{ predicateLabel: string; targetLabel: string }>> {
  const labelById = new Map<string, string>();
  for (const n of nodes) {
    labelById.set(n.id, n.label);
  }
  const out = new Map<string, Array<{ predicateLabel: string; targetLabel: string }>>();
  for (const e of edges) {
    const list = out.get(e.source) ?? [];
    list.push({
      predicateLabel: e.predicate_label ?? e.predicate,
      targetLabel:    labelById.get(e.target) ?? e.target,
    });
    out.set(e.source, list);
  }
  return out;
}


// ─── Triple List View ────────────────────────────────────────────────────────

interface EditState {
  nodeId: string;
  predicate: string;
  value: string;
  datatype: string | null;
  lang: string | null;
}

interface ListViewProps {
  testId: string;
  nodesByGroup: Record<string, GraphNode[]>;
  outgoingByNode: Map<string, Array<{ predicateLabel: string; targetLabel: string }>>;
  totalNodes: number;
  selectedNodeId: string | null;
  onSelect: (id: string) => void;
  runId: string;
  onTripleSaved: () => void;
}

/**
 * RDF triple table — grouped by node type. Each node row expands on
 * demand to show its literal properties (editable) and outgoing object
 * triples (read-only). Replaces the old summary-sentence list view.
 */
function ListView({
  testId,
  nodesByGroup,
  totalNodes,
  selectedNodeId,
  onSelect,
  runId,
  onTripleSaved,
}: ListViewProps) {
  const [expandedNodes, setExpandedNodes] = React.useState<Set<string>>(new Set());
  const [nodeDetails, setNodeDetails] = React.useState<Record<string, NodeDetail>>({});
  const [loadingNodes, setLoadingNodes] = React.useState<Set<string>>(new Set());
  const [editState, setEditState] = React.useState<EditState | null>(null);
  const [editValue, setEditValue] = React.useState("");
  const [savingTriple, setSavingTriple] = React.useState(false);
  const [saveResult, setSaveResult] = React.useState<{nodeId: string; predicate: string; ok: boolean; msg: string} | null>(null);
  const [overrides, setOverrides] = React.useState<Record<string, string>>({});

  React.useEffect(() => {
    if (!runId) return;
    fetch(`/api/runs/${runId}/rdf/overrides`)
      .then(r => r.ok ? r.json() : [])
      .then((rows: Array<{subject_uri: string; predicate_uri: string; new_value: string}>) => {
        const map: Record<string, string> = {};
        for (const r of rows) map[`${r.subject_uri}||${r.predicate_uri}`] = r.new_value;
        setOverrides(map);
      })
      .catch(() => {});
  }, [runId]);

  async function toggleNode(nodeId: string) {
    if (expandedNodes.has(nodeId)) {
      setExpandedNodes(prev => { const s = new Set(prev); s.delete(nodeId); return s; });
      return;
    }
    setExpandedNodes(prev => new Set(prev).add(nodeId));
    if (nodeDetails[nodeId]) return;
    setLoadingNodes(prev => new Set(prev).add(nodeId));
    try {
      const res = await fetch(`/api/runs/${runId}/rdf/node?id=${encodeURIComponent(nodeId)}`);
      if (res.ok) {
        const detail = await res.json() as NodeDetail;
        setNodeDetails(prev => ({...prev, [nodeId]: detail}));
      }
    } finally {
      setLoadingNodes(prev => { const s = new Set(prev); s.delete(nodeId); return s; });
    }
  }

  function startEdit(nodeId: string, predicate: string, value: string, datatype: string | null | undefined, lang: string | null) {
    setEditState({nodeId, predicate, value, datatype: datatype ?? null, lang});
    setEditValue(overrides[`${nodeId}||${predicate}`] ?? value);
    setSaveResult(null);
  }

  function cancelEdit() { setEditState(null); setSaveResult(null); }

  async function commitEdit() {
    if (!editState) return;
    setSavingTriple(true);
    try {
      const body: Record<string, string> = {
        subject_uri: editState.nodeId,
        predicate_uri: editState.predicate,
        new_value: editValue,
      };
      if (editState.datatype) body.new_datatype = editState.datatype;
      if (editState.lang) body.new_lang = editState.lang;
      const res = await fetch(`/api/runs/${runId}/rdf/triple`, {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await res.text());
      const key = `${editState.nodeId}||${editState.predicate}`;
      setOverrides(prev => ({...prev, [key]: editValue}));
      setSaveResult({nodeId: editState.nodeId, predicate: editState.predicate, ok: true, msg: "Saved ✓"});
      setEditState(null);
      onTripleSaved();
      setTimeout(() => setSaveResult(null), 2500);
    } catch (err) {
      setSaveResult({nodeId: editState.nodeId, predicate: editState.predicate, ok: false, msg: String(err)});
    } finally {
      setSavingTriple(false);
    }
  }

  return (
    <section data-testid={testId} className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-medium">RDF Triples ({totalNodes} nodes)</h3>
        <span className="text-xs muted">Click a node to expand its triples · ✏ to edit literal values</span>
      </div>
      <p className="text-xs muted">
        Built from <strong className="text-green-400">approved AI extraction</strong> + <strong className="text-blue-400">authority enrichment</strong> data.
        Edits below override literal values in the next rebuild.
      </p>

      {NODE_TYPE_GROUPS.map((g) => {
        const items = nodesByGroup[g.key] ?? [];
        if (items.length === 0) return null;
        return (
          <section key={g.key} className="space-y-1">
            <h4 className="kicker">{g.label} ({items.length})</h4>
            <div className="space-y-0.5">
              {items.map((n) => {
                const isExpanded = expandedNodes.has(n.id);
                const isLoading = loadingNodes.has(n.id);
                const detail = nodeDetails[n.id];
                const isSelected = selectedNodeId === n.id;
                return (
                  <div key={n.id} className={`rounded border border-white/5 ${isSelected ? "border-white/20" : ""}`}>
                    <button
                      type="button"
                      onClick={() => { void toggleNode(n.id); onSelect(n.id); }}
                      className="flex items-center gap-2 w-full px-3 py-1.5 text-left hover:bg-white/5 rounded"
                    >
                      <span className="text-xs text-white/40 select-none">{isExpanded ? "▼" : "▶"}</span>
                      <span className="w-2 h-2 rounded-full flex-shrink-0" style={{background: g.color}} />
                      <span className="text-sm text-ink font-medium truncate">{n.label}</span>
                      {isLoading && <span className="text-xs muted ml-auto">Loading…</span>}
                    </button>

                    {isExpanded && detail && (
                      <div className="px-3 pb-2">
                        {detail.types.length > 0 && (
                          <div className="text-xs muted mb-1.5">
                            rdf:type: {detail.types.map(t => t.label).join(", ")}
                          </div>
                        )}

                        {detail.properties.length > 0 && (
                          <table className="w-full text-xs border-collapse mb-2">
                            <thead>
                              <tr className="border-b border-white/10">
                                <th className="text-left py-1 pr-3 muted w-1/3">Predicate</th>
                                <th className="text-left py-1 muted">Value</th>
                                <th className="w-8" />
                              </tr>
                            </thead>
                            <tbody>
                              {detail.properties.map((prop, pi) => {
                                const overrideKey = `${n.id}||${prop.predicate}`;
                                const currentValue = overrides[overrideKey] ?? prop.value;
                                const isEdited = !!overrides[overrideKey];
                                const isBeingEdited =
                                  editState?.nodeId === n.id && editState.predicate === prop.predicate;
                                const result = saveResult?.nodeId === n.id && saveResult.predicate === prop.predicate
                                  ? saveResult : null;
                                return (
                                  <tr key={pi} className="border-b border-white/5 hover:bg-white/3">
                                    <td className="py-1 pr-3 muted font-mono align-top">{prop.predicate_label}</td>
                                    <td className="py-1 align-top">
                                      {isBeingEdited ? (
                                        <div className="flex items-center gap-1">
                                          <input
                                            type="text"
                                            value={editValue}
                                            onChange={e => setEditValue(e.target.value)}
                                            className="flex-1 bg-white/10 border border-white/20 rounded px-2 py-0.5 text-ink text-xs focus:outline-none focus:border-emerald-400"
                                            onKeyDown={e => { if (e.key === "Enter") void commitEdit(); if (e.key === "Escape") cancelEdit(); }}
                                            autoFocus
                                          />
                                          <button
                                            type="button"
                                            onClick={() => void commitEdit()}
                                            disabled={savingTriple}
                                            className="text-emerald-400 hover:text-emerald-300 px-1 disabled:opacity-50"
                                            title="Save"
                                          >✓</button>
                                          <button
                                            type="button"
                                            onClick={cancelEdit}
                                            className="text-red-400 hover:text-red-300 px-1"
                                            title="Cancel"
                                          >✕</button>
                                        </div>
                                      ) : (
                                        <span className={isEdited ? "text-amber-300" : "text-ink/80"}>
                                          {currentValue}
                                          {isEdited && <span className="ml-1 text-amber-500/70 text-[10px]">✏ edited</span>}
                                        </span>
                                      )}
                                      {result && (
                                        <div className={`text-[10px] mt-0.5 ${result.ok ? "text-emerald-400" : "text-red-400"}`}>
                                          {result.msg}
                                        </div>
                                      )}
                                    </td>
                                    <td className="py-1 text-center align-top">
                                      {!isBeingEdited && (
                                        <button
                                          type="button"
                                          onClick={() => startEdit(n.id, prop.predicate, currentValue, prop.datatype, null)}
                                          className="text-white/30 hover:text-white/70 text-[11px] px-1"
                                          title="Edit this triple value"
                                        >✏</button>
                                      )}
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        )}

                        {detail.outgoing.length > 0 && (
                          <details className="text-xs">
                            <summary className="cursor-pointer muted mb-1">
                              {detail.outgoing.length} object propert{detail.outgoing.length !== 1 ? "ies" : "y"}
                            </summary>
                            <table className="w-full border-collapse">
                              <tbody>
                                {detail.outgoing.map((o, oi) => (
                                  <tr key={oi} className="border-b border-white/5">
                                    <td className="py-0.5 pr-3 muted font-mono w-1/3">{o.predicate_label}</td>
                                    <td className="py-0.5 text-ink/70">{o.target_label ?? o.target_id ?? ""}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </details>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}
    </section>
  );
}


function Legend() {
  return (
    <GlassPill as="ul" className="absolute top-3 right-3 px-3 py-2 text-[10px] space-y-1 list-none m-0" aria-label="Node type legend">
      <li className="kicker">Legend</li>
      {LEGEND.map((l) => (
        <li key={l.type} className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className="inline-block w-3 h-3 rounded-full"
            style={{ background: l.color }}
          />
          <span>{l.type}</span>
        </li>
      ))}
    </GlassPill>
  );
}


function StatusPill({ status }: { status: string }) {
  const tone =
    status === "validated" ? "text-biu-sky"
    : status === "built" ? "text-yellow-300"
    : status === "error" ? "text-red-300"
    : "muted";
  return <GlassPill className={`px-3 py-1 text-[10px] kicker ${tone}`}>{status}</GlassPill>;
}


function groupBySeverity(violations: ShaclReport["violations"]) {
  const acc: Record<string, ShaclReport["violations"]> = {};
  for (const v of violations) {
    const key = v.severity || "Violation";
    (acc[key] ||= []).push(v);
  }
  return acc;
}
