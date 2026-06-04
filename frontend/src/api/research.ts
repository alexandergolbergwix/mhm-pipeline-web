import {api} from "./client";

export interface ResearchSummary {
  total_manuscripts: number;
  total_works: number;
  total_persons: number;
  total_places: number;
  persons_by_role: {scribe: number; owner: number; author: number};
  triples: number;
}

export interface CoOccurrencePair {
  ms: string;
  work1: string;
  work1_label: string;
  work2: string;
  work2_label: string;
}

export interface NetworkNode {
  id: string;
  label: string;
  role: "scribe" | "author" | "owner";
  ms_count: number;
}

export interface NetworkLink {
  source: string;
  target: string;
  ms: string;
}

export interface PeopleNetwork {
  nodes: NetworkNode[];
  links: NetworkLink[];
}

export interface OwnerChain {
  ms: string;
  ms_label: string;
  owners: {name: string; uri: string}[];
}

export interface GeoPoint {
  ms: string;
  ms_label: string;
  place: string;
  place_label: string;
  lat: number | null;
  lon: number | null;
  type: "production" | "mentioned";
}

const base = (projectId: string) => `/projects/${projectId}/research`;

export const researchApi = {
  summary:        (projectId: string) => api.get<ResearchSummary>(`${base(projectId)}/summary`),
  coOccurrence:   (projectId: string) => api.get<CoOccurrencePair[]>(`${base(projectId)}/co-occurrence`),
  peopleNetwork:  (projectId: string) => api.get<PeopleNetwork>(`${base(projectId)}/people-network`),
  ownership:      (projectId: string) => api.get<OwnerChain[]>(`${base(projectId)}/ownership`),
  geography:      (projectId: string) => api.get<GeoPoint[]>(`${base(projectId)}/geography`),
};
