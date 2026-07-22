import { api } from "@/api/client";
import type { RunJobSnapshot } from "@/api/runJobs";

export interface HmoManifestSummary {
  shelfmark: string;
  file: string;
  canvas_count: number;
  range_count: number;
  annotation_count: number;
  seealso_count: number;
}

export interface HmoBuildResult {
  manifest_count: number;
  total_canvases: number;
  total_ranges: number;
  total_annotations: number;
  manifest_dir: string;
  manifests: HmoManifestSummary[];
}

export interface HmoUploadOutcome {
  shelfmark: string;
  page_url: string;
  status:
    | "created"
    | "updated"
    | "unchanged"
    | "failed"
    | "dry_run"
    | string;
  message: string;
  edit_id: number | null;
  new_revid: number | null;
  canvas_count: number;
  range_count: number;
  annotation_count: number;
}

export interface HmoUploadResult {
  dry_run: boolean;
  uploaded: number;
  unchanged: number;
  failed: number;
  outcomes: HmoUploadOutcome[];
}

export interface HmoCoverageEntry {
  class_uri: string;
  class_local_name: string;
  class_label?: string;
  hmo_node_count: number;
  projection_status:
    | "direct_wikidata_item"
    | "summarized_in_wikidata"
    | "hmo_or_wikibase_only"
    | "unknown";
  wikidata_representation: string;
  wikidata_properties: string[];
  projected_item_count: number;
  notes: string;
  item_entity_type?: string;
}

export interface HmoCoverageReport {
  report_version: number;
  ttl_path: string;
  strategy_source: string;
  rdf_class_count: number;
  wikidata_item_count: number;
  wikidata_item_counts_by_type: Record<string, number>;
  classes: HmoCoverageEntry[];
}

export interface HmoStudioStatus {
  state: "idle" | "built" | "uploaded" | "error";
  rdf_present: boolean;
  manifest_count: number;
  coverage_present: boolean;
  last_upload_at: string | null;
  last_upload: HmoUploadResult | null;
  wikibase_configured: boolean;
  canonical_live_count: number;
  canonical_ready: boolean;
}

// ── Phase 4/5: full item export + upload ──────────────────────────────

export interface HmoResolvedClaim {
  property_id: string;
  datatype: string;
  value: unknown;
}

export interface HmoDeferredLink {
  source_local_id: string;
  property_id: string;
  target_local_id: string;
}

export interface HmoResolvedEntity {
  local_id: string;
  labels: Record<string, string>;
  descriptions: Record<string, string>;
  class_qid: string;
  source_uri: string;
  claims: HmoResolvedClaim[];
  deferred_links: HmoDeferredLink[];
  skipped_statements: string[];
}

export interface HmoItemBuildResult {
  from_cache: boolean;
  entity_count: number;
  deferred_link_count: number;
  skipped_statement_count: number;
  entities: HmoResolvedEntity[];
}

export interface HmoItemUploadOutcome {
  local_id: string;
  source_uri: string;
  status:
    | "created"
    | "updated"
    | "skipped"
    | "would_create"
    | "would_update"
    | "failed"
    | string;
  wikibase_id: string | null;
  message: string;
}

export interface HmoDeferredLinkOutcome {
  source_local_id: string;
  property_id: string;
  target_local_id: string;
  status: "linked" | "would_link" | "unresolved" | "failed" | string;
  message: string;
}

export interface HmoItemUploadResult {
  dry_run: boolean;
  created: number;
  updated: number;
  skipped: number;
  failed: number;
  blocked: number;
  linked: number;
  unresolved_links: number;
  outcomes: HmoItemUploadOutcome[];
  link_outcomes: HmoDeferredLinkOutcome[];
}

export interface HmoItemStatus {
  build_present: boolean;
  entity_count: number;
  deferred_link_count: number;
  uploaded_count: number;
  built_at: string | null;
}

/** A live upload spawns a background run job; a dry run returns the result inline. */
export function isItemUploadJob(
  r: HmoItemUploadResult | RunJobSnapshot,
): r is RunJobSnapshot {
  return "kind" in r && r.kind === "hmo_item_upload";
}

/** Rebuild an HmoItemUploadResult from a finished job's result payload. */
export function itemUploadResultFromJob(job: RunJobSnapshot): HmoItemUploadResult | null {
  const raw = job.result;
  if (!raw || typeof raw !== "object") return null;
  const outcomes = (raw as {outcomes?: unknown}).outcomes;
  if (!Array.isArray(outcomes)) return null;
  const links = (raw as {link_outcomes?: unknown}).link_outcomes;
  return {
    dry_run: false,
    created: Number((raw as {created?: unknown}).created ?? 0),
    updated: Number((raw as {updated?: unknown}).updated ?? 0),
    skipped: Number((raw as {skipped?: unknown}).skipped ?? 0),
    failed: Number((raw as {failed?: unknown}).failed ?? 0),
    blocked: Number((raw as {blocked?: unknown}).blocked ?? 0),
    linked: Number((raw as {linked?: unknown}).linked ?? 0),
    unresolved_links: Number((raw as {unresolved_links?: unknown}).unresolved_links ?? 0),
    outcomes: outcomes as HmoItemUploadOutcome[],
    link_outcomes: Array.isArray(links) ? (links as HmoDeferredLinkOutcome[]) : [],
  };
}

export const HmoStudio = {
  buildManifests: (runId: string) =>
    api.post<HmoBuildResult>(
      `/runs/${runId}/hmo-studio/build-manifests`, {},
    ),

  uploadManifests: (runId: string, dryRun: boolean) =>
    api.post<HmoUploadResult>(
      `/runs/${runId}/hmo-studio/upload-manifests`,
      { dry_run: dryRun },
    ),

  coverage: (runId: string) =>
    api.get<HmoCoverageReport>(`/runs/${runId}/hmo-studio/coverage`),

  status: (runId: string) =>
    api.get<HmoStudioStatus>(`/runs/${runId}/hmo-studio/status`),

  buildItems: (runId: string, forceRebuild = false, refreshAuthority = true) => {
    const params = new URLSearchParams();
    if (forceRebuild) params.set("force_rebuild", "true");
    if (refreshAuthority) params.set("refresh_authority", "true");
    const query = params.toString();
    return api.post<HmoItemBuildResult>(
      `/runs/${runId}/hmo-studio/build-items${query ? `?${query}` : ""}`,
      {},
    );
  },

  /**
   * Dry run returns the preview result inline. A live upload makes
   * thousands of sequential Wikibase Cloud writes — too slow for one HTTP
   * request — so the backend spawns a `run_jobs` background job and
   * returns its snapshot immediately; track `job.id` (e.g. via
   * `useRunJobAttachment`) for progress.
   *
   * `updateExisting` refreshes labels/descriptions and merges in any new
   * claims on already-uploaded items instead of skipping them.
   */
  uploadItems: (
    runId: string,
    dryRun: boolean,
    updateExisting = false,
    allowShaclErrors = false,
  ) =>
    api.post<HmoItemUploadResult | RunJobSnapshot>(
      `/runs/${runId}/hmo-studio/upload-items`,
      {
        dry_run: dryRun,
        update_existing: updateExisting,
        allow_shacl_errors: allowShaclErrors,
      },
    ),

  itemStatus: (runId: string) =>
    api.get<HmoItemStatus>(`/runs/${runId}/hmo-studio/item-status`),
};
