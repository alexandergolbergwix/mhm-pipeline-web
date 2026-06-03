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

import { useEffect, useMemo, useRef, useState } from "react";
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
  type RdfStatus,
  type ServerLayout,
  type ShaclReport,
} from "@/api/rdf";
import { SectionExportMenu } from "@/components/export/SectionExportMenu";
import { SectionImportButton } from "@/components/import/SectionImportButton";


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
const NODE_TYPE_GROUPS: Array<{ key: string; label: string }> = [
  { key: "Manuscript",    label: "Manuscripts" },
  { key: "Person",        label: "Persons" },
  { key: "Work",          label: "Works" },
  { key: "Place",         label: "Places" },
  { key: "Event",         label: "Events" },
  { key: "Organization",  label: "Organizations" },
  { key: "Codicological", label: "Codicological" },
  { key: "Other",         label: "Other" },
];


export default function StageRdf() {
  const { runId } = useParams<{ runId: string }>();

  const [status, setStatus] = useState<RdfStatus | null>(null);
  const [graph, setGraph]   = useState<GraphResponse | null>(null);
  const [shacl, setShacl]   = useState<ShaclReport | null>(null);
  const [error, setError]   = useState<string | null>(null);

  const [busy, setBusy] = useState<"build" | "validate" | "graph" | null>(null);
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
      await Rdf.build(runId);
      const [st, g] = await Promise.all([
        Rdf.status(runId), Rdf.graph(runId, 500, layout),
      ]);
      setStatus(st);
      setGraph(g);
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
        <section className="glass p-6 space-y-2">
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
        </section>

        <section className="glass p-6 space-y-4">
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
            <div className="glass-pill px-3 py-2 text-sm text-red-300">{error}</div>
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

          {/* Two-column body when a node is selected; full-width canvas otherwise. */}
          <div className={
            selectedNodeId && runId
              ? "grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-4"
              : "block"
          }>
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
              <div className="absolute top-3 left-3 glass-pill px-3 py-1 text-[10px] kicker text-yellow-300">
                Showing {graph.nodes.length} of {graph.total_nodes} nodes (top by degree)
              </div>
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
                <div className="glass-pill px-4 py-3 text-center space-y-1.5">
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
                </div>
              </div>
            )}
          </div>

          {/* LIST VIEW — semantic HTML alternative to the canvas. Same
              data, walkable heading-by-heading by a screen reader. */}
          {viewMode === "list" && (
            <ListView
              testId="list-view"
              nodesByGroup={nodesByGroup}
              outgoingByNode={outgoingByNode}
              totalNodes={totalNodes}
              onSelect={setSelectedNodeId}
              selectedNodeId={selectedNodeId}
            />
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
        </section>

        <section className="glass p-6 space-y-4">
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
        </section>
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


interface ListViewProps {
  testId: string;
  nodesByGroup: Record<string, GraphNode[]>;
  outgoingByNode: Map<string, Array<{ predicateLabel: string; targetLabel: string }>>;
  totalNodes: number;
  selectedNodeId: string | null;
  onSelect: (id: string) => void;
}

/**
 * Semantic HTML rendering of the graph — the WCAG 1.3.1 / 4.1.2
 * alternative to the opaque Cytoscape canvas. Headings + ul are the
 * structure a screen reader walks; each li is a button so the user
 * still gets parity with "click a node to select it".
 */
function ListView({
  testId,
  nodesByGroup,
  outgoingByNode,
  totalNodes,
  selectedNodeId,
  onSelect,
}: ListViewProps) {
  return (
    <section data-testid={testId} className="space-y-6">
      <h3 className="text-lg font-medium">Nodes ({totalNodes})</h3>
      {NODE_TYPE_GROUPS.map((g) => {
        const items = nodesByGroup[g.key] ?? [];
        if (items.length === 0) return null;
        return (
          <section key={g.key} className="space-y-2">
            <h4 className="kicker">{g.label} ({items.length})</h4>
            <ul className="space-y-1.5 text-sm">
              {items.map((n) => {
                const outgoing = outgoingByNode.get(n.id) ?? [];
                const grouped = groupOutgoingByPredicate(outgoing);
                const summary = grouped
                  .map((g2) => `${g2.predicateLabel}: ${g2.targets.join(", ")}`)
                  .join("; ");
                const isSelected = selectedNodeId === n.id;
                return (
                  <li key={n.id}>
                    <button
                      type="button"
                      onClick={() => onSelect(n.id)}
                      aria-pressed={isSelected}
                      className={`text-left w-full px-2 py-1 rounded ${
                        isSelected ? "bg-white/10 text-ink" : "hover:bg-white/5"
                      }`}
                    >
                      <span className="text-ink">{n.label}</span>
                      {summary && (
                        <>
                          {" — "}
                          <span className="muted">{summary}</span>
                        </>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </section>
  );
}


/**
 * Collapse repeats of the same predicate into one entry — so a node
 * with three "author of" edges renders as `author of: A, B, C`
 * rather than three separate `author of: A; author of: B;` clauses.
 */
function groupOutgoingByPredicate(
  outgoing: Array<{ predicateLabel: string; targetLabel: string }>,
): Array<{ predicateLabel: string; targets: string[] }> {
  const order: string[] = [];
  const acc = new Map<string, string[]>();
  for (const e of outgoing) {
    if (!acc.has(e.predicateLabel)) {
      acc.set(e.predicateLabel, []);
      order.push(e.predicateLabel);
    }
    acc.get(e.predicateLabel)!.push(e.targetLabel);
  }
  return order.map((p) => ({ predicateLabel: p, targets: acc.get(p) ?? [] }));
}


function Legend() {
  return (
    <ul
      aria-label="Node type legend"
      className="absolute top-3 right-3 glass-pill px-3 py-2 text-[10px] space-y-1 list-none m-0"
    >
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
    </ul>
  );
}


function StatusPill({ status }: { status: string }) {
  const tone =
    status === "validated" ? "text-biu-sky"
    : status === "built" ? "text-yellow-300"
    : status === "error" ? "text-red-300"
    : "muted";
  return <span className={`glass-pill px-3 py-1 text-[10px] kicker ${tone}`}>{status}</span>;
}


function groupBySeverity(violations: ShaclReport["violations"]) {
  const acc: Record<string, ShaclReport["violations"]> = {};
  for (const v of violations) {
    const key = v.severity || "Violation";
    (acc[key] ||= []).push(v);
  }
  return acc;
}
