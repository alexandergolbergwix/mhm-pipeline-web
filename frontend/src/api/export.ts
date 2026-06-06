/**
 * Typed client for the project-export endpoints.
 *
 * Export endpoints return `application/json` with
 * `Content-Disposition: attachment` so the browser streams the payload
 * directly to disk.  We trigger that by navigating a hidden anchor
 * rather than buffering the entire response into JS heap via `blob()`.
 *
 * Endpoints called (backend/app/routers/export.py):
 *   GET /api/projects/{id}/export?entity_types=...
 *   GET /api/projects/{id}/export/snapshots?entity_type=...&since=...
 *   GET /api/projects/{id}/export/history?entity_type=...&entity_id=...
 */

import { downloadFromUrl } from "@/utils/download";

export type ExportEntityType =
  | "marc_record"
  | "extraction_entity"
  | "authority_match"
  | "wikidata_override"
  | "wikibase_item";

export const EXPORT_ENTITY_TYPES: readonly ExportEntityType[] = [
  "marc_record",
  "extraction_entity",
  "authority_match",
  "wikidata_override",
  "wikibase_item",
] as const;

export const EXPORT_ENTITY_LABELS: Record<ExportEntityType, string> = {
  marc_record:        "MARC records",
  extraction_entity:  "Extraction entities",
  authority_match:    "Authority matches",
  wikidata_override:  "Wikidata overrides",
  wikibase_item:      "Wikibase items",
};

export interface SnapshotsOptions {
  entity_type?: ExportEntityType;
  since?: string;
}

export interface HistoryOptions {
  entity_type?: ExportEntityType;
  entity_id?: string;
}

export const Export = {
  /** Full project export.  `entityTypes` filters which entity-type
   *  collections are included; omitted / empty array = all five. */
  project(
    projectId: string,
    entityTypes?: ExportEntityType[],
  ): Promise<void> {
    const params = new URLSearchParams();
    if (entityTypes && entityTypes.length > 0) {
      for (const t of entityTypes) params.append("entity_types", t);
    }
    const qs = params.toString() ? `?${params.toString()}` : "";
    downloadFromUrl(
      `/api/projects/${projectId}/export${qs}`,
      `project-${projectId}-export.json`,
    );
    return Promise.resolve();
  },

  /** Snapshot export — point-in-time state of one entity type
   *  (or all five when `entity_type` is omitted).  `since` is an
   *  ISO-8601 timestamp; backend filters to snapshots newer than it. */
  snapshots(projectId: string, opts?: SnapshotsOptions): Promise<void> {
    const params = new URLSearchParams();
    if (opts?.entity_type) params.set("entity_type", opts.entity_type);
    if (opts?.since)       params.set("since", opts.since);
    const qs = params.toString() ? `?${params.toString()}` : "";
    downloadFromUrl(
      `/api/projects/${projectId}/export/snapshots${qs}`,
      `project-${projectId}-snapshots.json`,
    );
    return Promise.resolve();
  },

  /** Event-history export.  Pass `entity_type` + `entity_id` to scope
   *  to a single entity; omit both for the full project event log. */
  history(projectId: string, opts: HistoryOptions): Promise<void> {
    const params = new URLSearchParams();
    if (opts.entity_type) params.set("entity_type", opts.entity_type);
    if (opts.entity_id)   params.set("entity_id", opts.entity_id);
    const qs = params.toString() ? `?${params.toString()}` : "";
    downloadFromUrl(
      `/api/projects/${projectId}/export/history${qs}`,
      `project-${projectId}-history.json`,
    );
    return Promise.resolve();
  },
};
