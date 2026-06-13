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

export interface HeatmapPoint {
  place: string;
  place_label: string;
  lat: number;
  lon: number;
  weight: number;
  type: "production" | "mentioned" | "both";
}

export interface ProvenanceEvent {
  type: "production" | "ownership" | "current_holder";
  label: string;
  uri: string | null;
  year: number | null;
  year_earliest: number | null;
  year_latest: number | null;
  place: string | null;
  owner_birth?: number | null;
  owner_death?: number | null;
}

export interface ProvenanceTimeline {
  ms: string;
  ms_label: string | null;
  events: ProvenanceEvent[];
}

export interface ManuscriptPick {
  control_number: string;
  label: string | null;
  production_year: number | null;
}

export type MapStopKind =
  | "production"
  | "owner"
  | "significant_place"
  | "current_holder";

export interface MapStop {
  kind: MapStopKind;
  label: string;
  uri?: string | null;
  lat: number | null;
  lon: number | null;
  year?: number | null;
  year_earliest?: number | null;
  year_latest?: number | null;
  birth_year?: number | null;
  death_year?: number | null;
  certain: boolean;
  inferred_geo: boolean;
  geo_source?: string | null;
  geo_source_label?: string | null;
  approved?: boolean | null;
  is_present?: boolean;
  time: number | null;
  has_point: boolean;
}

export interface MapEdge {
  from: number;
  to: number;
  inferred: boolean;
  directed: boolean;
}

export interface ProvenanceMap {
  control_number: string;
  ms_label: string | null;
  stops: MapStop[];
  edges: MapEdge[];
  dropped: {label: string; reason: string}[];
}

// ── Corpus Movement (Phase 2) ────────────────────────────────────────────

export interface CorpusManuscript {
  control_number: string;
  label: string;
  production_lat: number | null;
  production_lon: number | null;
  production_year: number | null;
  production_year_earliest: number | null;
  production_year_latest: number | null;
  production_place: string | null;
  has_production_point: boolean;
  holder_lat: number;
  holder_lon: number;
  holder_label: string;
  genres: string[];
  owners: string[];
  places: string[];
}

export interface YearCount {
  year: number;
  count: number;
}

export interface CorpusMovement {
  manuscripts: CorpusManuscript[];
  year_counts: YearCount[];
}

export interface CorpusMovementFacets {
  year_min: number | null;
  year_max: number | null;
  places: string[];
  genres: string[];
  owners: string[];
}

export interface CorpusMovementFilters {
  from_year?: number;
  to_year?: number;
  place?: string;
  genre?: string;
  owner?: string;
}

export interface NeighborNode {
  uri: string;
  label: string | null;
  type: string;
  edge_type: string;
}

export interface PathNode {
  uri: string;
  label: string | null;
  type: string;
}

export interface PathEdge {
  source: string;
  target: string;
  label: string;
}

export interface PathResult {
  path: PathNode[];
  edges: PathEdge[];
}

const base = (projectId: string) => `/projects/${projectId}/research`;

export const researchApi = {
  summary:        (projectId: string) => api.get<ResearchSummary>(`${base(projectId)}/summary`),
  coOccurrence:   (projectId: string) => api.get<CoOccurrenceGraph>(`${base(projectId)}/co-occurrence`),
  peopleNetwork:  (projectId: string) => api.get<PeopleNetwork>(`${base(projectId)}/people-network`),
  ownership:      (projectId: string) => api.get<OwnerChain[]>(`${base(projectId)}/ownership`),
  geography:      (projectId: string) => api.get<PlaceRow[]>(`${base(projectId)}/geography`),
  geographyHeatmap: (projectId: string) => api.get<HeatmapPoint[]>(`${base(projectId)}/geography?mode=heatmap`),
  provenanceTimeline: (projectId: string, msUri: string, overlay?: string) =>
    api.get<ProvenanceTimeline>(
      `${base(projectId)}/provenance?ms=${encodeURIComponent(msUri)}${overlay ? `&overlay=${overlay}` : ""}`,
    ),
  manuscripts: (projectId: string) =>
    api.get<ManuscriptPick[]>(`${base(projectId)}/manuscripts`),
  provenanceMap: (projectId: string, cn: string, includeUnapproved = false) =>
    api.get<ProvenanceMap>(
      `${base(projectId)}/provenance-map?cn=${encodeURIComponent(cn)}${
        includeUnapproved ? "&include_unapproved=true" : ""
      }`,
    ),
  movementFacets: (projectId: string) =>
    api.get<CorpusMovementFacets>(`${base(projectId)}/movement/facets`),
  movement: (projectId: string, filters: CorpusMovementFilters = {}) => {
    const params = new URLSearchParams();
    if (filters.from_year != null) params.set("from_year", String(filters.from_year));
    if (filters.to_year != null)   params.set("to_year",   String(filters.to_year));
    if (filters.place)             params.set("place",     filters.place);
    if (filters.genre)             params.set("genre",     filters.genre);
    if (filters.owner)             params.set("owner",     filters.owner);
    const qs = params.toString();
    return api.get<CorpusMovement>(`${base(projectId)}/movement${qs ? `?${qs}` : ""}`);
  },
  neighbors: (projectId: string, uri: string) =>
    api.get<NeighborNode[]>(`${base(projectId)}/neighbors?uri=${encodeURIComponent(uri)}`),
  shortestPath: (projectId: string, fromUri: string, toUri: string) =>
    api.get<PathResult>(
      `${base(projectId)}/path?from=${encodeURIComponent(fromUri)}&to=${encodeURIComponent(toUri)}`,
    ),
};
