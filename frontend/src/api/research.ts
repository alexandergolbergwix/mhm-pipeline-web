import {api} from "./client";

export interface ResearchSummary {
  total_manuscripts: number;
  total_works: number;
  total_persons: number;
  total_places: number;
  persons_by_role: {scribe: number; owner: number; author: number};
  triples: number;
}

/** @deprecated Use CoOccurrenceGraph instead — the server now returns
 *  a pre-aggregated graph rather than raw pairs. */
export interface CoOccurrencePair {
  ms: string;
  work1: string;
  work1_label: string;
  work2: string;
  work2_label: string;
}

export interface CoOccurrenceNode {
  id: string;
  label: string;
  degree: number;
}

export interface CoOccurrenceEdge {
  work1: string;
  work2: string;
  shared_ms_count: number;
  ms_list: string[];
}

export interface CoOccurrenceGraph {
  nodes: CoOccurrenceNode[];
  edges: CoOccurrenceEdge[];
}

export interface NetworkNode {
  id: string;
  label: string;
  role: "scribe" | "author" | "owner";
  ms_count: number;
  /** Pre-computed spring-layout position (server-side networkx). */
  x: number;
  /** Pre-computed spring-layout position (server-side networkx). */
  y: number;
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

/** @deprecated The server now returns PlaceRow (grouped). */
export interface GeoPoint {
  ms: string;
  ms_label: string;
  place: string;
  place_label: string;
  lat: number | null;
  lon: number | null;
  type: "production" | "mentioned";
}

export interface PlaceRow {
  place: string;
  place_label: string;
  lat: number | null;
  lon: number | null;
  type: "production" | "mentioned";
  ms_count: number;
  ms_labels: string[];
}

const base = (projectId: string) => `/projects/${projectId}/research`;

export const researchApi = {
  summary:        (projectId: string) => api.get<ResearchSummary>(`${base(projectId)}/summary`),
  coOccurrence:   (projectId: string) => api.get<CoOccurrenceGraph>(`${base(projectId)}/co-occurrence`),
  peopleNetwork:  (projectId: string) => api.get<PeopleNetwork>(`${base(projectId)}/people-network`),
  ownership:      (projectId: string) => api.get<OwnerChain[]>(`${base(projectId)}/ownership`),
  geography:      (projectId: string) => api.get<PlaceRow[]>(`${base(projectId)}/geography`),
};
