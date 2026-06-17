/**
 * RDF Graph — RDF graph client.
 *
 * Five endpoints under ``/runs/{id}/rdf/``:
 *
 *   POST /build              → triples_count + manuscripts_count
 *   GET  /graph              → Cytoscape JSON (nodes + edges + truncation flag)
 *   GET  /download.ttl       → static URL (downloadUrl helper)
 *   POST /validate           → SHACL report
 *   GET  /status             → idle | built | validated | error
 *
 * The graph response is Cytoscape-shaped already; the route component
 * passes it straight to ``react-cytoscapejs``.
 */

import { api } from "@/api/client";


export interface GraphNode {
  id: string;
  label: string;
  type: string;
  color: string;
  properties?: Record<string, string[]>;
  /** Server-computed position. Cytoscape uses ``preset`` layout to
   *  pin these so we don't pay for a browser-side layout pass. */
  position?: { x: number; y: number };
}


export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  predicate: string;
  predicate_label?: string;
}


export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
  total_nodes: number;
  total_edges: number;
  layout?: string;
}


// Layouts the BACKEND can compute. The browser no longer runs layouts.
export type ServerLayout =
  | "spring"
  | "kamada_kawai"
  | "circular"
  | "shell"
  | "concentric";


// One RDF type attached to a node (rdf:type triple).
export interface NodeTypeRef {
  uri:   string;
  label: string;
}

// Datatype property: literal value with a predicate.
export interface NodeProperty {
  predicate:       string;
  predicate_label: string;
  value:           string;
  datatype?:       string | null;
}

// One incoming or outgoing edge to/from another node.
export interface NodeEdgeRef {
  predicate:       string;
  predicate_label: string;
  target_id?:      string;     // outgoing direction
  target_label?:   string;
  target_type?:    string;
  target_color?:   string;
  source_id?:      string;     // incoming direction
  source_label?:   string;
  source_type?:    string;
  source_color?:   string;
}

export interface NodeDetail {
  id:         string;
  label:      string;
  type:       string;
  color:      string;
  types:      NodeTypeRef[];
  properties: NodeProperty[];
  outgoing:   NodeEdgeRef[];
  incoming:   NodeEdgeRef[];
}


export interface ShaclViolation {
  focus_node: string;
  source_shape: string;
  severity: string;        // "Violation" | "Warning" | "Info"
  message: string;
  value: string | null;
}


export interface ShaclReport {
  conforms: boolean;
  violations: ShaclViolation[];
}


export interface RdfBuildResponse {
  triples_count: number;
  manuscripts_count: number;
  output_path: string;
  started_at: string;
  finished_at: string;
  mapping_errors: string[];
  coverage_path?: string | null;
  unknown_class_count?: number | null;
}


export interface RdfBuildOptions {
  add_epistemological_status?: boolean;
  add_cataloging_view?: boolean;
  add_philological_overlay?: boolean;
}


export interface RdfCoverageClass {
  class_uri: string;
  class_local_name: string;
  hmo_node_count: number;
  projection_status: string;
  wikidata_representation?: string;
  notes?: string;
}


export interface RdfCoverageResponse {
  rdf_class_count: number;
  unknown_class_count: number;
  classes: RdfCoverageClass[];
}


export interface RdfStatus {
  status: "idle" | "built" | "validated" | "error" | string;
  triples_count?: number | null;
  manuscripts_count?: number | null;
  last_built_at?: string | null;
  error?: string | null;
}


export interface TripleOverrideRequest {
  subject_uri: string;
  predicate_uri: string;
  new_value: string;
  new_datatype?: string;
  new_lang?: string;
}

export interface TripleOverrideResponse {
  id: string;
  subject_uri: string;
  predicate_uri: string;
  new_value: string;
  new_datatype?: string;
  new_lang?: string;
  old_value?: string;
  created_at: string;
}

export async function saveTripleOverride(
  runId: string,
  body: TripleOverrideRequest,
): Promise<TripleOverrideResponse> {
  const res = await fetch(`/api/runs/${runId}/rdf/triple`, {
    method: "PATCH",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<TripleOverrideResponse>;
}

export async function listTripleOverrides(runId: string): Promise<TripleOverrideResponse[]> {
  const res = await fetch(`/api/runs/${runId}/rdf/overrides`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<TripleOverrideResponse[]>;
}


export const Rdf = {
  build: (runId: string, options?: RdfBuildOptions) =>
    api.post<RdfBuildResponse>(`/runs/${runId}/rdf/build`, options ?? {}),

  coverage: (runId: string) =>
    api.get<RdfCoverageResponse>(`/runs/${runId}/rdf/coverage`),

  graph: (runId: string, maxNodes = 500, layout: ServerLayout = "spring") =>
    api.get<GraphResponse>(
      `/runs/${runId}/rdf/graph?max_nodes=${maxNodes}&layout=${layout}`,
    ),

  /** Fetch full detail for one node (clicked in the graph view).
   *  ``nodeId`` is passed as a query param so URIs with slashes work
   *  without encoding the whole path. */
  node: (runId: string, nodeId: string) =>
    api.get<NodeDetail>(
      `/runs/${runId}/rdf/node?id=${encodeURIComponent(nodeId)}`,
    ),

  validate: (runId: string) =>
    api.post<ShaclReport>(`/runs/${runId}/rdf/validate`, {}),

  status: (runId: string) =>
    api.get<RdfStatus>(`/runs/${runId}/rdf/status`),

  downloadUrl: (runId: string) =>
    `/api/runs/${runId}/rdf/download.ttl`,
};
