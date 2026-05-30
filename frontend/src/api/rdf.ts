/**
 * Stage 4 — RDF graph client.
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
}


export interface RdfStatus {
  status: "idle" | "built" | "validated" | "error" | string;
  triples_count?: number | null;
  manuscripts_count?: number | null;
  last_built_at?: string | null;
  error?: string | null;
}


export const Rdf = {
  build: (runId: string) =>
    api.post<RdfBuildResponse>(`/runs/${runId}/rdf/build`, {}),

  graph: (runId: string, maxNodes = 500) =>
    api.get<GraphResponse>(`/runs/${runId}/rdf/graph?max_nodes=${maxNodes}`),

  validate: (runId: string) =>
    api.post<ShaclReport>(`/runs/${runId}/rdf/validate`, {}),

  status: (runId: string) =>
    api.get<RdfStatus>(`/runs/${runId}/rdf/status`),

  downloadUrl: (runId: string) =>
    `/api/runs/${runId}/rdf/download.ttl`,
};
