import { api } from "./client";

export interface ManuscriptRef {
  uri: string;
  label: string | null;
  role: string;
}

export interface EntityDetailResponse {
  uri: string;
  label: string | null;
  type: string;
  roles: string[];
  manuscripts: ManuscriptRef[];
  /** Birth/death years (persons) or production bounds (works) */
  dates: { birth?: number; death?: number } | null;
  /** Geographic coordinates (places only) */
  geo: { lat: number; lon: number } | null;
  /** External authority identifiers — viaf, wikidata, mazal, … */
  identifiers: Record<string, string>;
}

export const entityApi = {
  getEntityDetail: (projectId: string, uri: string) =>
    api.get<EntityDetailResponse>(
      `/projects/${projectId}/research/entity?uri=${encodeURIComponent(uri)}`,
    ),
};
