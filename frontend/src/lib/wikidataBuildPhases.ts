export interface BuildPhaseHint {
  detail: string;
  expected: string;
}

/**
 * Curator-facing guide for the Wikidata Studio build phases, keyed by the
 * backend phase label (wikidata_studio_build_job BUILD_PHASES). Durations
 * reflect the 18.5k-entity reference run on the Modal 8GB container.
 */
export const WIKIDATA_BUILD_PHASE_HINTS: Record<string, BuildPhaseHint> = {
  "loading records": {
    detail: "Loading MARC records, authority matches, and curator overrides",
    expected: "~1 min",
  },
  "loading canonical entities": {
    detail: "Loading the durable HMO read-back entities that anchor the projection",
    expected: "~1 min",
  },
  "preparing transliterations": {
    detail: "Preparing Hebrew/Latin name transliterations",
    expected: "~1 min",
  },
  "building items": {
    detail: "Building one Wikidata item per record — per-record progress shows below",
    expected: "~3 min",
  },
  "assembling canonical projection": {
    detail: "Deduplicating and cross-referencing all entities in memory. This is the longest phase, and it has no per-record progress — the bar stays still while it works",
    expected: "~15–25 min",
  },
  "mining provenance prose": {
    detail: "Extracting provenance statements for the section export",
    expected: "~5 min",
  },
};
