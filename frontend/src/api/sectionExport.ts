/**
 * Per-section export API client.
 *
 * Each method triggers a native browser streaming download via a
 * hidden anchor rather than buffering the full response into heap.
 *
 * Endpoints called (backend/app/routers/section_export.py):
 *   GET /api/runs/{id}/extraction/export?format=json|csv&approved_only=...
 *   GET /api/runs/{id}/authority/export?format=json|csv&approved_only=...
 *   GET /api/runs/{id}/rdf/export?format=ttl|nt
 *   GET /api/runs/{id}/wikibase/export?format=json|csv|ttl
 *   GET /api/runs/{id}/wikidata-studio/export?format=json|csv|ttl&approved_only=...
 */

import { downloadFromUrl } from "@/utils/download";

export type ExtractionExportFormat = "json" | "csv";
export type AuthorityExportFormat = "json" | "csv";
export type RdfExportFormat = "ttl" | "nt";
export type WikibaseExportFormat = "json" | "csv" | "ttl";
export type WikidataExportFormat = "json" | "csv" | "ttl";

export const SectionExport = {
  extraction(
    runId: string,
    format: ExtractionExportFormat = "json",
    approvedOnly = false,
  ): Promise<void> {
    const params = new URLSearchParams({format, approved_only: String(approvedOnly)});
    downloadFromUrl(
      `/api/runs/${runId}/extraction/export?${params}`,
      `run-${runId}-extraction-${approvedOnly ? "approved" : "all"}.${format}`,
    );
    return Promise.resolve();
  },

  authority(
    runId: string,
    format: AuthorityExportFormat = "json",
    approvedOnly = false,
  ): Promise<void> {
    const params = new URLSearchParams({format, approved_only: String(approvedOnly)});
    downloadFromUrl(
      `/api/runs/${runId}/authority/export?${params}`,
      `run-${runId}-authority-${approvedOnly ? "approved" : "all"}.${format}`,
    );
    return Promise.resolve();
  },

  rdf(runId: string, format: RdfExportFormat = "ttl"): Promise<void> {
    const params = new URLSearchParams({format});
    downloadFromUrl(
      `/api/runs/${runId}/rdf/export?${params}`,
      `run-${runId}-manuscripts.${format}`,
    );
    return Promise.resolve();
  },

  wikibase(runId: string, format: WikibaseExportFormat = "json"): Promise<void> {
    const params = new URLSearchParams({format});
    downloadFromUrl(
      `/api/runs/${runId}/wikibase/export?${params}`,
      `run-${runId}-wikibase.${format}`,
    );
    return Promise.resolve();
  },

  wikidataStudio(
    runId: string,
    format: WikidataExportFormat = "json",
    approvedOnly = true,
  ): Promise<void> {
    const params = new URLSearchParams({format, approved_only: String(approvedOnly)});
    downloadFromUrl(
      `/api/runs/${runId}/wikidata-studio/export?${params}`,
      `run-${runId}-wikidata-${approvedOnly ? "approved" : "all"}.${format}`,
    );
    return Promise.resolve();
  },
};
