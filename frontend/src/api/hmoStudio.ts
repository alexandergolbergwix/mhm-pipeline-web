import { api } from "@/api/client";

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
  bot_username_set: boolean;
  bot_password_set: boolean;
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
};
