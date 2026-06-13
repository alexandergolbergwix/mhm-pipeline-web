import {api} from "./client";

export interface SavedQuery {
  id: string;
  project_id: string;
  created_by: string | null;
  name: string;
  description: string;
  query: string;
  params: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SavedQueryCreate {
  name: string;
  description?: string;
  query: string;
  params?: Record<string, unknown>;
}

export interface SavedQueryUpdate {
  name?: string;
  description?: string;
  query?: string;
  params?: Record<string, unknown>;
}

const _base = (projectId: string) =>
  `/projects/${projectId}/research/saved-queries`;

export const savedQueriesApi = {
  list: (projectId: string) => api.get<SavedQuery[]>(_base(projectId)),

  create: (projectId: string, body: SavedQueryCreate) =>
    api.post<SavedQuery>(_base(projectId), body),

  get: (projectId: string, queryId: string) =>
    api.get<SavedQuery>(`${_base(projectId)}/${queryId}`),

  update: (projectId: string, queryId: string, body: SavedQueryUpdate) =>
    api.put<SavedQuery>(`${_base(projectId)}/${queryId}`, body),

  delete: (projectId: string, queryId: string) =>
    api.del<void>(`${_base(projectId)}/${queryId}`),
};

/** Extract {{placeholder}} names from a query string. */
export function extractParams(query: string): string[] {
  const matches = query.match(/\{\{(\w+)\}\}/g) ?? [];
  return [...new Set(matches.map((m) => m.slice(2, -2)))];
}

/** Substitute {{param}} placeholders with the provided values. */
export function substituteParams(query: string, values: Record<string, string>): string {
  return query.replace(/\{\{(\w+)\}\}/g, (_, name) => values[name] ?? `{{${name}}}`);
}
