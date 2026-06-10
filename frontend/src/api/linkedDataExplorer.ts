import {api} from "./client";

export type SparqlDataSource = "hmo" | "wikibase" | "wikidata";

export interface SparqlResponse {
  columns: string[];
  rows: (string | null)[][];
  truncated: boolean;
}

const _endpoint = (projectId: string, source: SparqlDataSource) => {
  if (source === "wikibase") return `/projects/${projectId}/research/sparql/wikibase`;
  if (source === "wikidata") return `/projects/${projectId}/research/sparql/wikidata`;
  return `/projects/${projectId}/research/sparql`;
};

export const linkedDataExplorerApi = {
  executeSparql: (projectId: string, query: string, source: SparqlDataSource) =>
    api.post<SparqlResponse>(_endpoint(projectId, source), {query}),
};
