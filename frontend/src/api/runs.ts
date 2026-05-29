import { api } from "@/api/client";

export type RunStatus = "pending" | "running" | "succeeded" | "failed";

export interface RunListItem {
  id: string;
  project_id: string;
  name: string;
  status: RunStatus;
  record_count: number;
  match_count: number;
  error: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface AuthorityMatch {
  id: string;
  control_number: string;
  entity_text: string;
  entity_kind: string;
  role: string;
  matched_name: string;
  mazal_id: string;
  viaf_id: string;
  wikidata_qid: string;
  confidence: "high" | "medium" | "low" | string;
  source: string;
  payload: Record<string, unknown>;
  approved: boolean;
  approved_by: string | null;
  approved_at: string | null;
}

export interface RunDetail extends RunListItem {
  matches: AuthorityMatch[];
}

export interface RunMarcRecord {
  control_number: string;
  marc: Record<string, unknown>;
}

export const Runs = {
  listForProject: (projectId: string) =>
    api.get<RunListItem[]>(`/projects/${projectId}/runs`),

  create: async (projectId: string, file: File): Promise<RunListItem> => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/projects/${projectId}/runs`, {
      method: "POST",
      credentials: "include",
      body: fd,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const d = (await res.json()) as { detail?: string };
        if (d?.detail) detail = d.detail;
      } catch { /* ignore */ }
      throw new Error(detail);
    }
    return (await res.json()) as RunListItem;
  },

  get: (id: string) => api.get<RunDetail>(`/runs/${id}`),

  listMatches: (id: string) => api.get<AuthorityMatch[]>(`/runs/${id}/matches`),

  getRecord: (id: string, controlNumber: string) =>
    api.get<RunMarcRecord>(`/runs/${id}/records/${encodeURIComponent(controlNumber)}`),

  setApproval: (runId: string, matchId: string, approved: boolean) =>
    api.patch<AuthorityMatch>(`/runs/${runId}/matches/${matchId}`, { approved }),

  bulkApprove: (runId: string, matchIds: string[], approved: boolean) =>
    api.post<AuthorityMatch[]>(`/runs/${runId}/matches/bulk-approve`, {
      match_ids: matchIds, approved,
    }),
};
