import {api} from "./client";

export type SparqlDataSource = "hmo" | "wikibase" | "wikidata" | "corpus";
export type ExportFormat = "csv" | "json" | "bibtex" | "ris";

export interface SparqlResponse {
  columns: string[];
  rows: (string | null)[][];
  truncated: boolean;
}

const _endpoint = (projectId: string, source: SparqlDataSource) => {
  if (source === "wikibase") return `/projects/${projectId}/research/sparql/wikibase`;
  if (source === "wikidata") return `/projects/${projectId}/research/sparql/wikidata`;
  if (source === "corpus")   return `/research/corpus/sparql`;
  return `/projects/${projectId}/research/sparql`;
};

/** Trigger a browser download of the SPARQL results in the given format. */
export async function exportResults(
  projectId: string,
  query: string,
  format: ExportFormat,
): Promise<void> {
  const resp = await fetch(`/api/projects/${projectId}/research/sparql/export`, {
    method: "POST",
    credentials: "include",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({query, format}),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => resp.statusText);
    throw new Error(`Export failed (${resp.status}): ${text}`);
  }
  const blob = await resp.blob();
  const ext: Record<ExportFormat, string> = {csv: "csv", json: "json", bibtex: "bib", ris: "ris"};
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `sparql-export.${ext[format]}`;
  a.click();
  URL.revokeObjectURL(url);
}

export interface EvidenceApproval {
  source: string;
  entity_text: string;
  approved_by: string | null;
  approved_at: string | null;
}

export interface EvidenceAuthorityMatch {
  entity_text: string | null;
  entity_kind: string | null;
  matched_name: string | null;
  wikidata_qid: string | null;
  viaf_id: string | null;
  confidence: string | null;
  source: string | null;
}

export interface EvidenceResponse {
  uri: string;
  control_number: string | null;
  marc: Record<string, unknown> | null;
  approvals: EvidenceApproval[];
  authority_matches: EvidenceAuthorityMatch[];
}

/** Corpus rows come as dicts; normalize to arrays matching SparqlResponse shape. */
function _normalizeCorpusResponse(raw: {columns: string[]; rows: Record<string, string | null>[]}): SparqlResponse {
  const rows = raw.rows.map((row) => raw.columns.map((col) => row[col] ?? null));
  return {columns: raw.columns, rows, truncated: false};
}

export const linkedDataExplorerApi = {
  executeSparql: async (projectId: string, query: string, source: SparqlDataSource): Promise<SparqlResponse> => {
    if (source === "corpus") {
      const raw = await api.post<{columns: string[]; rows: Record<string, string | null>[]}>(
        _endpoint(projectId, source), {query},
      );
      return _normalizeCorpusResponse(raw);
    }
    return api.post<SparqlResponse>(_endpoint(projectId, source), {query});
  },

  getEvidence: (projectId: string, uri: string) =>
    api.get<EvidenceResponse>(
      `/projects/${projectId}/research/evidence?uri=${encodeURIComponent(uri)}`,
    ),
};
