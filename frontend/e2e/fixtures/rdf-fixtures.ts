/**
 * Playwright route mocks for the RDF graph review surface.
 * Every backend endpoint StageRdf touches is mocked deterministically.
 *
 * The mock graph carries:
 *   - 30 nodes across all 8 ontology types
 *   - 40 edges across 5 distinct predicates (covers chip-row + overflow)
 *   - 2 SHACL violations on known node ids (for the SHACL chip)
 *
 * State is mutable so individual tests can flip the page from
 * "built" → "validated" without re-installing mocks.
 */
import type { Page, Route } from "@playwright/test";

export const TEST_RUN_ID = "11111111-1111-1111-1111-111111111111";

export interface MockGraphNode {
  id: string;
  label: string;
  type: string;
  color: string;
  properties?: Record<string, string[]>;
  position?: { x: number; y: number };
}
export interface MockGraphEdge {
  id: string;
  source: string;
  target: string;
  predicate: string;
  predicate_label: string;
}
export interface MockGraphResponse {
  nodes: MockGraphNode[];
  edges: MockGraphEdge[];
  truncated: boolean;
  total_nodes: number;
  total_edges: number;
}

const COLORS: Record<string, string> = {
  Manuscript:    "#77cce5",
  Person:        "#f6c177",
  Work:          "#c4a7e7",
  Place:         "#9ccfd8",
  Event:         "#eb6f92",
  Organization:  "#f6d6c5",
  Codicological: "#a3e0bc",
  Other:         "#cfd2da",
};

function n(id: string, label: string, type: string,
           pos: [number, number],
           props?: Record<string, string[]>): MockGraphNode {
  return {
    id,
    label,
    type,
    color: COLORS[type] ?? "#cfd2da",
    position: { x: pos[0], y: pos[1] },
    properties: props,
  };
}

function e(id: string, source: string, target: string,
           predicate: string, predicate_label: string): MockGraphEdge {
  return { id, source, target, predicate, predicate_label };
}

export function makeMockGraph(): MockGraphResponse {
  const nodes: MockGraphNode[] = [
    // Manuscripts (5)
    n("urn:ms:1", "MS 1", "Manuscript", [0,   0], { shelfmark: ["NLI 1"] }),
    n("urn:ms:2", "MS 2", "Manuscript", [200, 0], { shelfmark: ["NLI 2"] }),
    n("urn:ms:3", "MS 3", "Manuscript", [400, 0], { shelfmark: ["NLI 3"] }),
    n("urn:ms:4", "MS 4", "Manuscript", [600, 0], { shelfmark: ["NLI 4"] }),
    n("urn:ms:5", "MS 5", "Manuscript", [800, 0], { shelfmark: ["NLI 5"] }),

    // Persons (8) — Maimonides is the search anchor
    n("urn:p:1", "Maimonides",         "Person", [0,   200], { nli: ["NNL-001"] }),
    n("urn:p:2", "Rashi",              "Person", [200, 200]),
    n("urn:p:3", "Yehuda HaLevi",      "Person", [400, 200]),
    n("urn:p:4", "Abraham ibn Ezra",   "Person", [600, 200]),
    n("urn:p:5", "Joseph Karo",        "Person", [800, 200]),
    n("urn:p:6", "Isaac Luria",        "Person", [1000, 200]),
    n("urn:p:7", "Moses Mendelssohn",  "Person", [1200, 200]),
    n("urn:p:8", "Baal Shem Tov",      "Person", [1400, 200]),

    // Works (5)
    n("urn:w:1", "Mishneh Torah",     "Work", [0,   400]),
    n("urn:w:2", "Kuzari",            "Work", [200, 400]),
    n("urn:w:3", "Shulchan Aruch",    "Work", [400, 400]),
    n("urn:w:4", "Zohar",             "Work", [600, 400]),
    n("urn:w:5", "Tanya",             "Work", [800, 400]),

    // Places (4)
    n("urn:pl:1", "Cordoba",          "Place", [0,   600]),
    n("urn:pl:2", "Jerusalem",        "Place", [200, 600]),
    n("urn:pl:3", "Safed",            "Place", [400, 600]),
    n("urn:pl:4", "Prague",           "Place", [600, 600]),

    // Events (2)
    n("urn:ev:1", "Copy event 1",     "Event", [0,   800]),
    n("urn:ev:2", "Copy event 2",     "Event", [200, 800]),

    // Organizations (2)
    n("urn:org:1", "NLI",             "Organization", [0,   1000]),
    n("urn:org:2", "Bodleian",        "Organization", [200, 1000]),

    // Codicological units (2)
    n("urn:cu:1", "Codicological 1",  "Codicological", [0,   1200]),
    n("urn:cu:2", "Codicological 2",  "Codicological", [200, 1200]),

    // Other (2) — also test the overflow-popup predicate path
    n("urn:o:1", "Other 1", "Other", [0,   1400]),
    n("urn:o:2", "Other 2", "Other", [200, 1400]),
  ];

  const edges: MockGraphEdge[] = [
    // owned by (12 edges) — most-frequent predicate
    e("e1",  "urn:ms:1", "urn:p:1", "hmo:owned_by", "owned by"),
    e("e2",  "urn:ms:2", "urn:p:2", "hmo:owned_by", "owned by"),
    e("e3",  "urn:ms:3", "urn:p:3", "hmo:owned_by", "owned by"),
    e("e4",  "urn:ms:4", "urn:p:4", "hmo:owned_by", "owned by"),
    e("e5",  "urn:ms:5", "urn:p:5", "hmo:owned_by", "owned by"),
    e("e6",  "urn:ms:1", "urn:p:6", "hmo:owned_by", "owned by"),
    e("e7",  "urn:ms:2", "urn:p:7", "hmo:owned_by", "owned by"),
    e("e8",  "urn:ms:3", "urn:p:8", "hmo:owned_by", "owned by"),
    e("e9",  "urn:ms:4", "urn:p:1", "hmo:owned_by", "owned by"),
    e("e10", "urn:ms:5", "urn:p:2", "hmo:owned_by", "owned by"),
    e("e11", "urn:ms:1", "urn:p:3", "hmo:owned_by", "owned by"),
    e("e12", "urn:ms:2", "urn:p:4", "hmo:owned_by", "owned by"),

    // author of (5)
    e("e13", "urn:p:1", "urn:w:1", "hmo:author_of", "author of"),
    e("e14", "urn:p:3", "urn:w:2", "hmo:author_of", "author of"),
    e("e15", "urn:p:5", "urn:w:3", "hmo:author_of", "author of"),
    e("e16", "urn:p:6", "urn:w:4", "hmo:author_of", "author of"),
    e("e17", "urn:p:8", "urn:w:5", "hmo:author_of", "author of"),

    // contains work (5)
    e("e18", "urn:ms:1", "urn:w:1", "hmo:has_part", "contains work"),
    e("e19", "urn:ms:2", "urn:w:2", "hmo:has_part", "contains work"),
    e("e20", "urn:ms:3", "urn:w:3", "hmo:has_part", "contains work"),
    e("e21", "urn:ms:4", "urn:w:4", "hmo:has_part", "contains work"),
    e("e22", "urn:ms:5", "urn:w:5", "hmo:has_part", "contains work"),

    // copied at (4)
    e("e23", "urn:ms:1", "urn:pl:1", "hmo:copied_at", "copied at"),
    e("e24", "urn:ms:2", "urn:pl:2", "hmo:copied_at", "copied at"),
    e("e25", "urn:ms:3", "urn:pl:3", "hmo:copied_at", "copied at"),
    e("e26", "urn:ms:4", "urn:pl:4", "hmo:copied_at", "copied at"),

    // held by (2)
    e("e27", "urn:ms:1", "urn:org:1", "hmo:held_by", "held by"),
    e("e28", "urn:ms:2", "urn:org:2", "hmo:held_by", "held by"),

    // copy event (2)
    e("e29", "urn:ms:1", "urn:ev:1", "hmo:copy_event", "copy event"),
    e("e30", "urn:ms:2", "urn:ev:2", "hmo:copy_event", "copy event"),

    // codicological (2)
    e("e31", "urn:ms:1", "urn:cu:1", "hmo:has_unit",   "has codicological unit"),
    e("e32", "urn:ms:2", "urn:cu:2", "hmo:has_unit",   "has codicological unit"),

    // related to (4) — overflow-popup territory at 9 distinct preds
    e("e33", "urn:p:1", "urn:p:3", "hmo:related_to", "related to"),
    e("e34", "urn:p:2", "urn:p:4", "hmo:related_to", "related to"),
    e("e35", "urn:p:5", "urn:p:6", "hmo:related_to", "related to"),
    e("e36", "urn:p:7", "urn:p:8", "hmo:related_to", "related to"),

    // mentions (4)
    e("e37", "urn:w:1", "urn:o:1", "hmo:mentions",  "mentions"),
    e("e38", "urn:w:2", "urn:o:2", "hmo:mentions",  "mentions"),
    e("e39", "urn:w:3", "urn:o:1", "hmo:mentions",  "mentions"),
    e("e40", "urn:w:4", "urn:o:2", "hmo:mentions",  "mentions"),
  ];

  return {
    nodes,
    edges,
    truncated: false,
    total_nodes: nodes.length,
    total_edges: edges.length,
    manuscript_count: 5,
    manuscripts_in_view: 5,
  };
}

export function makeMockCatalog(graph: MockGraphResponse) {
  const node_types: Record<string, number> = {};
  for (const n of graph.nodes) {
    node_types[n.type] = (node_types[n.type] ?? 0) + 1;
  }
  const edge_predicates: Record<string, number> = {};
  for (const e of graph.edges) {
    edge_predicates[e.predicate_label] = (edge_predicates[e.predicate_label] ?? 0) + 1;
  }
  return {
    total_nodes: graph.total_nodes,
    total_edges: graph.total_edges,
    node_types,
    edge_predicates,
    manuscript_count: graph.manuscript_count ?? node_types.Manuscript ?? 0,
    f4_singleton_count: graph.manuscripts_in_view ?? node_types.Manuscript ?? 0,
  };
}

export interface RdfMockState {
  status: "idle" | "built" | "validated";
  graph: MockGraphResponse;
  shaclConforms: boolean;
  shaclViolations: Array<{
    focus_node: string;
    source_shape: string;
    severity: string;
    message: string;
    value: string | null;
  }>;
}

export function makeRdfState(): RdfMockState {
  return {
    status: "built",
    graph: makeMockGraph(),
    shaclConforms: false,
    shaclViolations: [
      {
        focus_node: "urn:ms:1",
        source_shape: "ManuscriptShape",
        severity: "Violation",
        message: "Missing required property",
        value: null,
      },
      {
        focus_node: "urn:p:1",
        source_shape: "PersonShape",
        severity: "Warning",
        message: "Author lacks death date",
        value: null,
      },
    ],
  };
}

export async function installRdfMocks(page: Page, state: RdfMockState) {
  await page.route("**/api/auth/me", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "33333333-3333-3333-3333-333333333333",
        email: "test@example.org",
        name: "Test User",
        role: "editor",
      }),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/rdf/status`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: state.status,
        triples_count: state.graph.edges.length,
        manuscripts_count: 5,
      }),
    });
  });

  await page.route(/\/api\/runs\/[^/]+\/rdf\/(graph|viewport)(\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    const types = url.searchParams.getAll("types");
    const predicates = url.searchParams.getAll("predicates");
    const q = (url.searchParams.get("q") || "").toLowerCase();

    let nodes = state.graph.nodes;
    let edges = state.graph.edges;

    if (types.length > 0) {
      nodes = nodes.filter((n) => types.includes(n.type));
    }
    if (q) {
      const searchNodeIds = new Set<string>();
      for (const n of nodes) {
        const hay = [
          n.label,
          n.type,
          ...Object.values(n.properties ?? {}).flat(),
        ].join(" ").toLowerCase();
        if (hay.includes(q)) searchNodeIds.add(n.id);
      }
      for (const e of edges) {
        const lbl = (e.predicate_label || e.predicate || "").toLowerCase();
        if (lbl.includes(q)) {
          searchNodeIds.add(e.source);
          searchNodeIds.add(e.target);
        }
      }
      nodes = nodes.filter((n) => searchNodeIds.has(n.id));
    }
    const nodeIds = new Set(nodes.map((n) => n.id));
    edges = edges.filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target));
    if (predicates.length > 0) {
      edges = edges.filter((e) => predicates.includes(e.predicate_label));
      const edgeIds = new Set<string>();
      for (const e of edges) {
        edgeIds.add(e.source);
        edgeIds.add(e.target);
      }
      nodes = nodes.filter((n) => edgeIds.has(n.id));
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...state.graph,
        nodes,
        edges,
        truncated: nodes.length < state.graph.total_nodes,
        manuscripts_in_view: nodes.filter((n) => n.type === "Manuscript").length,
      }),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/rdf/catalog`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeMockCatalog(state.graph)),
    });
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/rdf/validate`, async (route) => {
    state.status = "validated";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        conforms:   state.shaclConforms,
        violations: state.shaclViolations,
      }),
    });
  });

  await page.route(/\/api\/runs\/[^/]+\/rdf\/node(\?.*)?$/, async (route) => {
    // NodeDetailPanel might fire — return a minimal payload so it
    // doesn't error. Test specs don't assert on its content.
    const url = new URL(route.request().url());
    const id = url.searchParams.get("id") || "urn:ms:1";
    const node = state.graph.nodes.find((n) => n.id === id) ?? state.graph.nodes[0];
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id:         node.id,
        label:      node.label,
        type:       node.type,
        color:      node.color,
        types:      [{ uri: "urn:type", label: node.type }],
        properties: Object.entries(node.properties ?? {}).map(([predicate, values]) => ({
          predicate,
          predicate_label: predicate,
          value: values[0] ?? "",
        })),
        outgoing: [],
        incoming: [],
      }),
    });
  });
}

export async function gotoRdf(page: Page) {
  await page.context().addCookies([
    {
      name: "session",
      value: "test-session",
      url: page.url() !== "about:blank" ? page.url() : "http://localhost:5173",
    },
  ]).catch(() => {});
  await page.goto(`/runs/${TEST_RUN_ID}/rdf`);
}
