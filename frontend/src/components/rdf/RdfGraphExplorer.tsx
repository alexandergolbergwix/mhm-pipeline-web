/**
 * Embeddable RDF graph explorer (viewport + GraphFilters + Cytoscape / list).
 * Build / validate / SHACL stay on the RDF Graph page — this panel is read-mostly.
 */

import {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {Link} from "react-router-dom";
import {type Core, type LayoutOptions, type StylesheetJsonBlock} from "cytoscape";
import CytoscapeComponent from "react-cytoscapejs";

import {ApiError} from "@/api/client";
import {
  Rdf,
  type GraphCatalogResponse,
  type GraphNode,
  type GraphResponse,
  type RdfStatus,
  type ServerLayout,
  type CanvasBudget,
} from "@/api/rdf";
import {NodeDetailPanel} from "@/components/NodeDetailPanel";
import {GraphFilters} from "@/components/rdf/GraphFilters";
import {
  emptyFilterState,
  useGraphFilters,
  type GraphFilterState,
} from "@/components/rdf/useGraphFilters";
import {Glass, GlassPill} from "@/components/glass";

const LAYOUT_NAMES: Array<{value: ServerLayout; label: string}> = [
  {value: "spring", label: "Force (spring)"},
  {value: "kamada_kawai", label: "Force (kamada-kawai)"},
  {value: "shell", label: "Concentric (by class)"},
  {value: "circular", label: "Circular"},
];

const CANVAS_BUDGETS: Array<{value: CanvasBudget; label: string}> = [
  {value: 500, label: "500 nodes"},
  {value: 1000, label: "1,000 nodes"},
  {value: 2000, label: "2,000 nodes"},
];

const LEGEND: Array<{type: string; color: string}> = [
  {type: "Manuscript", color: "#77cce5"},
  {type: "Person", color: "#f6c177"},
  {type: "Work", color: "#c4a7e7"},
  {type: "Place", color: "#9ccfd8"},
  {type: "Event", color: "#eb6f92"},
  {type: "Organization", color: "#f6d6c5"},
  {type: "Codicological", color: "#a3e0bc"},
  {type: "Other", color: "#cfd2da"},
];

export type RdfGraphExplorerProps = {
  runId: string;
  height?: number;
  className?: string;
};

export function RdfGraphExplorer({
  runId,
  height = 480,
  className,
}: RdfGraphExplorerProps) {
  const [status, setStatus] = useState<RdfStatus | null>(null);
  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [catalog, setCatalog] = useState<GraphCatalogResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [layout, setLayout] = useState<ServerLayout>("spring");
  const [canvasBudget, setCanvasBudget] = useState<CanvasBudget>(500);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"canvas" | "list">("canvas");
  const [filterState, setFilterState] = useState<GraphFilterState>(() => emptyFilterState());
  const cyRef = useRef<Core | null>(null);
  const viewportRequestRef = useRef(0);

  const filterKey = useMemo(
    () => [
      [...filterState.types].sort().join("|"),
      [...filterState.predicates].sort().join("|"),
      filterState.query,
      filterState.radius,
      filterState.shaclOnly ? "1" : "0",
    ].join("::"),
    [filterState],
  );

  const manuscriptsOnly = useMemo(
    () => filterState.types.size === 1 && filterState.types.has("Manuscript"),
    [filterState.types],
  );

  const emptyShacl = useMemo(() => new Set<string>(), []);

  const activeSets = useGraphFilters({
    nodes: graph?.nodes ?? [],
    edges: graph?.edges ?? [],
    state: filterState,
    shaclFocus: emptyShacl,
    selectedId: selectedNodeId,
    serverFiltered: true,
  });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const st = await Rdf.status(runId);
        if (cancelled) return;
        setStatus(st);
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.detail : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [runId]);

  useEffect(() => {
    if (status?.status !== "built" && status?.status !== "validated") return;
    let cancelled = false;
    void Rdf.catalog(runId)
      .then((c) => { if (!cancelled) setCatalog(c); })
      .catch(() => { if (!cancelled) setCatalog(null); });
    return () => { cancelled = true; };
  }, [runId, status?.status]);

  useEffect(() => {
    if (status?.status !== "built" && status?.status !== "validated") return;
    const requestId = ++viewportRequestRef.current;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const payload = await Rdf.viewport(runId, {
          types: manuscriptsOnly ? [] : [...filterState.types],
          predicates: [...filterState.predicates],
          q: filterState.query,
          maxNodes: canvasBudget,
          layout,
          manuscriptsOnly,
        });
        if (requestId !== viewportRequestRef.current) return;
        setGraph(payload);
      } catch (e) {
        if (requestId !== viewportRequestRef.current) return;
        setError(e instanceof ApiError ? e.detail : String(e));
      } finally {
        if (requestId === viewportRequestRef.current) setLoading(false);
      }
    })();
  }, [runId, status?.status, layout, filterKey, filterState, canvasBudget, manuscriptsOnly]);

  const elements = useMemo(() => {
    if (!graph) return [];
    return [
      ...graph.nodes.map((n) => ({
        data: n,
        position: n.position ?? undefined,
      })),
      ...graph.edges.map((e) => ({data: e})),
    ];
  }, [graph]);

  const stylesheet = useMemo<StylesheetJsonBlock[]>(() => ([
    {
      selector: "node",
      style: {
        "background-color": "data(color)",
        label: "data(label)",
        color: "#e8edf4",
        "font-size": 9,
        "text-valign": "bottom",
        "text-halign": "center",
        "text-margin-y": 4,
        "text-wrap": "ellipsis",
        "text-max-width": "120px",
        "text-opacity": 1,
        "text-background-color": "rgba(0,16,8,0.75)",
        "text-background-opacity": 0.75,
        "text-background-shape": "roundrectangle",
        "text-background-padding": "2px",
        "border-width": 1,
        "border-color": "rgba(0, 0, 0, 0.25)",
        width: "32px",
        height: "32px",
      },
    },
    {
      selector: "node:selected",
      style: {"border-width": 3, "border-color": "#77cce5"},
    },
    {
      selector: "edge",
      style: {
        width: 1,
        "line-color": "rgba(183, 216, 227, 0.4)",
        "curve-style": "bezier",
        "target-arrow-shape": "triangle",
        "target-arrow-color": "rgba(183, 216, 227, 0.55)",
        "arrow-scale": 0.7,
        label: "",
        "font-size": 8,
        color: "#b7d8e3",
      },
    },
    {
      selector: "edge.show-label, edge:selected, edge.hover",
      style: {label: "data(predicate_label)"},
    },
    {selector: "node.dim", style: {opacity: 0.15, "text-opacity": 0.15}},
    {selector: "edge.dim", style: {opacity: 0.1}},
  ]), []);

  const layoutOptions = useMemo<LayoutOptions>(() => ({
    name: "preset",
    fit: true,
    padding: 30,
  } as unknown as LayoutOptions), []);

  const attachCy = useCallback((cy: Core) => {
    cyRef.current = cy;
    const applyEdgeLabelClass = () => {
      cy.edges().toggleClass("show-label", cy.zoom() > 0.6);
    };
    cy.on("zoom", applyEdgeLabelClass);
    applyEdgeLabelClass();
    cy.on("mouseover", "edge", (evt) => evt.target.addClass("hover"));
    cy.on("mouseout", "edge", (evt) => evt.target.removeClass("hover"));
    cy.on("tap", "node", (evt) => {
      const id = evt.target.id();
      if (id) setSelectedNodeId(id);
    });
    cy.on("tap", (evt) => {
      if (evt.target === cy) setSelectedNodeId(null);
    });
  }, []);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || elements.length === 0) return;
    const handle = window.requestAnimationFrame(() => {
      try {
        cy.layout(layoutOptions).run();
        cy.fit(undefined, 30);
      } catch {
        /* ignore */
      }
    });
    return () => window.cancelAnimationFrame(handle);
  }, [elements, layoutOptions]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !graph) return;
    cy.batch(() => {
      cy.nodes().forEach((n) => {
        n.toggleClass("dim", !activeSets.nodeIds.has(n.id()));
      });
      cy.edges().forEach((e) => {
        const src = e.source().id();
        const tgt = e.target().id();
        e.toggleClass("dim", !activeSets.nodeIds.has(src) || !activeSets.nodeIds.has(tgt));
      });
    });
  }, [activeSets.nodeIds, graph]);

  const handleSearchChange = useCallback((query: string) => {
    setFilterState((prev) => ({...prev, query}));
  }, []);

  const statusLabel = status?.status ?? "idle";
  const graphReady = statusLabel === "built" || statusLabel === "validated";

  if (!loading && statusLabel === "idle") {
    return (
      <Glass as="section" className={`p-6 space-y-3 ${className ?? ""}`} data-testid="hmo-rdf-explorer">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <div className="kicker">Corpus RDF graph</div>
            <h3 className="text-lg font-medium">No graph yet</h3>
          </div>
          <Link to={`/runs/${runId}/rdf`} className="text-biu-sky hover:underline text-sm">
            Open RDF Graph to build →
          </Link>
        </div>
        <p className="muted text-sm">
          Build the RDF graph on the RDF Graph page, then return here to explore it with filters.
        </p>
      </Glass>
    );
  }

  return (
    <div className={`space-y-3 ${className ?? ""}`} data-testid="hmo-rdf-explorer">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="kicker">Corpus RDF graph</div>
          <h3 className="text-lg font-medium">
            {graph
              ? `${graph.total_nodes.toLocaleString()} nodes · ${graph.total_edges.toLocaleString()} edges`
              : "Graph explorer"}
          </h3>
        </div>
        <Link to={`/runs/${runId}/rdf`} className="text-biu-sky hover:underline text-sm">
          Full RDF Graph page →
        </Link>
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}

      {graphReady && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <label className="muted flex items-center gap-1">
            Layout
            <select
              className="input-glass text-xs"
              value={layout}
              onChange={(e) => setLayout(e.target.value as ServerLayout)}
            >
              {LAYOUT_NAMES.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </label>
          <label className="muted flex items-center gap-1">
            Budget
            <select
              className="input-glass text-xs"
              value={canvasBudget}
              onChange={(e) => setCanvasBudget(Number(e.target.value) as CanvasBudget)}
            >
              {CANVAS_BUDGETS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="button-ghost text-xs"
            onClick={() => cyRef.current?.fit(undefined, 30)}
          >
            Fit
          </button>
          <button
            type="button"
            className="button-ghost text-xs"
            onClick={() => setViewMode((m) => (m === "canvas" ? "list" : "canvas"))}
          >
            {viewMode === "canvas" ? "List view" : "Canvas view"}
          </button>
        </div>
      )}

      {graph && graph.nodes.length > 0 && (
        <GraphFilters
          nodes={graph.nodes}
          edges={graph.edges}
          catalog={catalog}
          shaclFocus={emptyShacl}
          selectedId={selectedNodeId}
          state={filterState}
          onChange={setFilterState}
          onSearchChange={handleSearchChange}
          visibleCount={activeSets.nodeIds.size}
          totalCount={graph.nodes.length}
          corpusTotal={catalog?.total_nodes ?? graph.total_nodes}
        />
      )}

      {loading && <p className="muted text-sm">Loading graph…</p>}

      {graph && graph.nodes.length > 0 && viewMode === "canvas" && (
        <div className="relative w-full" style={{height}}>
          <CytoscapeComponent
            elements={elements}
            cy={attachCy}
            layout={layoutOptions}
            stylesheet={stylesheet}
            style={{
              width: "100%",
              height: "100%",
              background: "rgba(0, 16, 8, 0.4)",
              borderRadius: "20px",
              border: "1px solid var(--line)",
            }}
          />
        </div>
      )}

      {graph && graph.nodes.length > 0 && viewMode === "list" && (
        <Glass variant="compact" className="p-3 max-h-[28rem] overflow-auto space-y-2">
          {groupNodes(graph.nodes).map(([type, nodes]) => (
            <div key={type}>
              <div className="kicker mb-1">{type} · {nodes.length}</div>
              <ul className="space-y-1">
                {nodes.slice(0, 80).map((n) => (
                  <li key={n.id}>
                    <button
                      type="button"
                      className={`text-left text-sm w-full truncate ${
                        selectedNodeId === n.id ? "text-biu-sky" : "hover:text-ink"
                      }`}
                      onClick={() => setSelectedNodeId(n.id)}
                    >
                      {n.label}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </Glass>
      )}

      <div className="flex flex-wrap gap-2">
        {LEGEND.map((item) => (
          <GlassPill key={item.type} className="px-2 py-0.5 text-[10px] muted">
            <span
              className="inline-block w-2 h-2 rounded-full mr-1"
              style={{background: item.color}}
            />
            {item.type}
          </GlassPill>
        ))}
      </div>

      {selectedNodeId && (
        <NodeDetailPanel
          runId={runId}
          nodeId={selectedNodeId}
          onClose={() => setSelectedNodeId(null)}
          onNavigate={(id) => setSelectedNodeId(id)}
        />
      )}
    </div>
  );
}

function groupNodes(nodes: GraphNode[]): Array<[string, GraphNode[]]> {
  const order = [
    "Manuscript", "Person", "Work", "Place",
    "Event", "Organization", "Codicological", "Other",
  ];
  const acc: Record<string, GraphNode[]> = {};
  for (const key of order) acc[key] = [];
  for (const n of nodes) {
    const key = order.includes(n.type) ? n.type : "Other";
    (acc[key] ||= []).push(n);
  }
  return order
    .filter((k) => (acc[k] ?? []).length > 0)
    .map((k) => [k, acc[k] ?? []]);
}
