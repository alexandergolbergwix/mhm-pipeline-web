import { api } from "@/api/client";

export interface StudioSummary {
  total_items: number;
  manuscripts: number;
  persons: number;
  works: number;
  statements: number;
}

export interface StudioItem {
  labels?: Record<string, string>;
  descriptions?: Record<string, string>;
  aliases?: Record<string, string[]>;
  statements?: Array<{
    property?: string;
    property_id?: string;
    value?: unknown;
    value_id?: string;
    value_type?: string;
    rank?: string;
    qualifiers?: unknown[];
    references?: unknown[];
  }>;
  existing_qid?: string | null;
  entity_type?: string;
}

export interface StudioBuild {
  items: StudioItem[];
  quickstatements: string;
  summary: StudioSummary;
  approved_match_count: number;
  record_count: number;
}

export const Studio = {
  build: (runId: string) => api.get<StudioBuild>(`/runs/${runId}/wikidata-studio`),
  qsUrl: (runId: string) => `/api/runs/${runId}/wikidata-studio/quickstatements.txt`,
};
