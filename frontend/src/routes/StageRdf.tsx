/**
 * Stage 4 · RDF Graph — per-run page.
 *
 * Build → Visualise → Validate → Download. The Cytoscape canvas is
 * the headline; SHACL findings collapse underneath.
 *
 * Layout selectors registered at module load (see registerLayouts).
 * The graph endpoint already returns Cytoscape-compatible node/edge
 * dicts, so we just wrap them in the ``{data: …}`` shape the React
 * component expects.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import cytoscape, { type Core, type LayoutOptions, type StylesheetJsonBlock } from "cytoscape";
import coseBilkent from "cytoscape-cose-bilkent";
import dagre from "cytoscape-dagre";
import CytoscapeComponent from "react-cytoscapejs";

import { Layout } from "@/components/Layout";
import { ApiError } from "@/api/client";
import {
  Rdf,
  type GraphResponse,
  type RdfStatus,
  type ShaclReport,
} from "@/api/rdf";


// Register layout extensions once. The plugin export is the function
// cytoscape's static API expects — guard against double-registration
// during Vite HMR.
let LAYOUTS_REGISTERED = false;
function registerLayouts() {
  if (LAYOUTS_REGISTERED) return;
  // The two plugins call ``cytoscape("layout", name, impl)`` themselves.
  cytoscape.use(coseBilkent);
  cytoscape.use(dagre);
  LAYOUTS_REGISTERED = true;
}
registerLayouts();


type LayoutName = "cose-bilkent" | "cose" | "dagre" | "concentric" | "breadthfirst";


const LAYOUT_NAMES: Array<{ value: LayoutName; label: string }> = [
  { value: "cose-bilkent", label: "Force (cose-bilkent)" },
  { value: "cose",         label: "Force (cose)" },
  { value: "dagre",        label: "Hierarchical (dagre)" },
  { value: "concentric",   label: "Concentric" },
  { value: "breadthfirst", label: "Breadth-first" },
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


export default function StageRdf() {
  const { runId } = useParams<{ runId: string }>();

  const [status, setStatus] = useState<RdfStatus | null>(null);
  const [graph, setGraph]   = useState<GraphResponse | null>(null);
  const [shacl, setShacl]   = useState<ShaclReport | null>(null);
  const [error, setError]   = useState<string | null>(null);

  const [busy, setBusy] = useState<"build" | "validate" | "graph" | null>(null);
  // Default = concentric: deterministic, finishes in <100ms even on 500
  // nodes, and ALWAYS positions every node visibly. Force layouts
  // (cose / cose-bilkent) are gorgeous but block the canvas while they
  // run; pick them from the dropdown when you want the spider-web look.
  const [layout, setLayout] = useState<LayoutName>("concentric");
  const [shaclOpen, setShaclOpen] = useState(false);

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

  async function loadGraph() {
    if (!runId) return;
    setBusy("graph"); setError(null);
    try {
      setGraph(await Rdf.graph(runId));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function build() {
    if (!runId) return;
    setBusy("build"); setError(null);
    try {
      await Rdf.build(runId);
      const [st, g] = await Promise.all([Rdf.status(runId), Rdf.graph(runId)]);
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

  const elements = useMemo(() => {
    if (!graph) return [];
    return [
      ...graph.nodes.map((n) => ({ data: n })),
      ...graph.edges.map((e) => ({ data: e })),
    ];
  }, [graph]);

  const stylesheet = useMemo<StylesheetJsonBlock[]>(() => ([
    {
      selector: "node",
      style: {
        "background-color":  "data(color)",
        "label":             "data(label)",
        "color":             "#0c1014",
        "font-size":         10,
        "text-valign":       "center",
        "text-halign":       "center",
        "text-wrap":         "ellipsis",
        "text-max-width":    "80px",
        "text-opacity":      0,         // hidden by default; revealed at zoom + hover
        "border-width":      1,
        "border-color":      "rgba(0, 0, 0, 0.25)",
        "width":             "32px",
        "height":            "32px",
      },
    },
    {
      selector: "node:selected",
      style: {
        "border-width": 3,
        "border-color": "#77cce5",
        "text-opacity": 1,
      },
    },
    {
      selector: "edge",
      style: {
        "width":              1,
        "line-color":         "rgba(183, 216, 227, 0.4)",
        "curve-style":        "bezier",
        "target-arrow-shape": "triangle",
        "target-arrow-color": "rgba(183, 216, 227, 0.55)",
        "arrow-scale":        0.7,
        "label":              "",
        "font-size":          8,
        "color":              "#b7d8e3",
        "text-background-color": "#001008",
        "text-background-opacity": 0.8,
        "text-background-padding": "2px",
      },
    },
    {
      selector: "edge:selected, edge.hover",
      style: {
        "width":      2,
        "line-color": "#77cce5",
        "label":      "data(predicate_label)",
      },
    },
  ]), []);

  const layoutOptions = useMemo<LayoutOptions>(() => {
    const base = {
      name:    layout,
      animate: false,
      fit:     true,
      padding: 30,
    };
    if (layout === "cose-bilkent") {
      // Bounded iterations: with 500 nodes and 1500+ edges the
      // unbounded sim runs for tens of seconds and the canvas stays
      // blank in the meantime. 2000 iterations is plenty for a
      // visually-coherent layout and finishes in ~1s.
      return { ...base, name: "cose-bilkent",
        nodeRepulsion: 4500, idealEdgeLength: 80,
        numIter: 2000, randomize: true, quality: "default",
      } as unknown as LayoutOptions;
    }
    if (layout === "cose") {
      // Stock cose has a similar issue — bound runtime so the page
      // doesn't appear empty.
      return { ...base, name: "cose",
        numIter: 1500, animate: false,
      } as unknown as LayoutOptions;
    }
    if (layout === "dagre") {
      return { ...base, name: "dagre", rankDir: "LR", nodeSep: 40, rankSep: 70 } as unknown as LayoutOptions;
    }
    return base as LayoutOptions;
  }, [layout]);

  // Reveal node labels as the user zooms in. Hover always reveals.
  function attachCy(cy: Core) {
    cyRef.current = cy;
    cy.on("zoom", () => {
      const z = cy.zoom();
      cy.nodes().style("text-opacity", z > 0.6 ? 1 : 0);
    });
    cy.on("mouseover", "node", (evt) => {
      evt.target.style("text-opacity", 1);
    });
    cy.on("mouseout", "node", (evt) => {
      const z = cy.zoom();
      if (z <= 0.6) evt.target.style("text-opacity", 0);
    });
    cy.on("mouseover", "edge", (evt) => evt.target.addClass("hover"));
    cy.on("mouseout",  "edge", (evt) => evt.target.removeClass("hover"));
  }

  // ── React-cytoscapejs quirk: the ``layout`` prop only runs once at
  // mount. When elements load asynchronously (status fetch → setGraph
  // → re-render), the canvas adds the nodes but never positions them
  // — every node sits at (0,0). Solution: when elements arrive, run a
  // fresh layout against the cy instance, then fit + center.
  // Also re-runs when the user picks a different layout from the
  // dropdown.
  const [layoutRunning, setLayoutRunning] = useState(false);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || elements.length === 0) return;
    setLayoutRunning(true);
    // Give the layout extension time to register if HMR just re-loaded.
    const handle = window.requestAnimationFrame(() => {
      try {
        const lay = cy.layout(layoutOptions);
        lay.one("layoutstop", () => {
          cy.fit(undefined, 30);
          setLayoutRunning(false);
        });
        lay.run();
        // Synchronous layouts (concentric / breadthfirst / grid) don't
        // emit layoutstop the way force layouts do — fit immediately
        // as well so the user always sees the graph.
        cy.fit(undefined, 30);
      } catch (exc) {
        console.warn("cy layout failed:", exc);
        setLayoutRunning(false);
      }
    });
    return () => window.cancelAnimationFrame(handle);
  }, [elements, layoutOptions]);

  function fitToScreen() {
    cyRef.current?.fit(undefined, 30);
  }

  const statusLabel = status?.status ?? "idle";
  const violations  = shacl?.violations ?? [];
  const grouped     = useMemo(() => groupBySeverity(violations), [violations]);

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
              <span className="muted text-sm ml-2">
                Layout:&nbsp;
                <select value={layout}
                        onChange={(e) => setLayout(e.target.value as LayoutName)}
                        className="input-glass !py-1 !w-auto text-xs inline">
                  {LAYOUT_NAMES.map((l) => (
                    <option key={l.value} value={l.value}>{l.label}</option>
                  ))}
                </select>
              </span>
              <button onClick={fitToScreen}
                      disabled={!graph || elements.length === 0}
                      title="Recenter + zoom to fit the visible graph"
                      className="button-ghost text-sm">
                Fit
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

          <div className="relative w-full" style={{ height: "600px" }}>
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
            {!graph && busy !== "build" && (
              <div className="absolute inset-0 flex items-center justify-center">
                <p className="muted">
                  {statusLabel === "idle"
                    ? "No graph yet — click Build RDF."
                    : busy === "graph" ? "Loading graph…" : "Click Build RDF to refresh."}
                </p>
              </div>
            )}
            {graph && layoutRunning && (
              <div className="absolute top-3 right-3 glass-pill px-3 py-1 text-[10px] kicker text-biu-sky">
                Computing layout…
              </div>
            )}
          </div>
        </section>

        <section className="glass p-6 space-y-4">
          <button
            onClick={() => setShaclOpen((v) => !v)}
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
            shacl == null ? (
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
            )
          )}
        </section>
      </div>
    </Layout>
  );
}


function Legend() {
  return (
    <div className="absolute top-3 right-3 glass-pill px-3 py-2 text-[10px] space-y-1">
      <div className="kicker">Legend</div>
      {LEGEND.map((l) => (
        <div key={l.type} className="flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-full"
                style={{ background: l.color }} />
          <span>{l.type}</span>
        </div>
      ))}
    </div>
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
