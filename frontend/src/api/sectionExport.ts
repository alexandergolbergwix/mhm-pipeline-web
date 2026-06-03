/**
 * Per-section export API client.
 *
 * All functions trigger a browser download by calling the shared
 * ``downloadAttachment`` helper (same pattern as ``api/export.ts``).
 *
 * Endpoints called (backend/app/routers/section_export.py):
 *   GET /api/runs/{id}/extraction/export?format=json|csv&approved_only=...
 *   GET /api/runs/{id}/authority/export?format=json|csv&approved_only=...
 *   GET /api/runs/{id}/rdf/export?format=ttl|nt
 *   GET /api/runs/{id}/wikibase/export?format=json|csv|ttl
 *   GET /api/runs/{id}/wikidata-studio/export?format=json|csv|ttl&approved_only=...
 */

import {csrfHeaders} from "@/api/client";

export type ExtractionExportFormat = "json" | "csv";
export type AuthorityExportFormat = "json" | "csv";
export type RdfExportFormat = "ttl" | "nt";
export type WikibaseExportFormat = "json" | "csv" | "ttl";
export type WikidataExportFormat = "json" | "csv" | "ttl";

export const SectionExport = {
  async extraction(
    runId: string,
    format: ExtractionExportFormat = "json",
    approvedOnly = false,
  ): Promise<void> {
    const params = new URLSearchParams({format, approved_only: String(approvedOnly)});
    await downloadAttachment(
      `/api/runs/${runId}/extraction/export?${params}`,
      `run-${runId}-extraction-${approvedOnly ? "approved" : "all"}.${format}`,
    );
  },

  async authority(
    runId: string,
    format: AuthorityExportFormat = "json",
    approvedOnly = false,
  ): Promise<void> {
    const params = new URLSearchParams({format, approved_only: String(approvedOnly)});
    await downloadAttachment(
      `/api/runs/${runId}/authority/export?${params}`,
      `run-${runId}-authority-${approvedOnly ? "approved" : "all"}.${format}`,
    );
  },

  async rdf(runId: string, format: RdfExportFormat = "ttl"): Promise<void> {
    const params = new URLSearchParams({format});
    await downloadAttachment(
      `/api/runs/${runId}/rdf/export?${params}`,
      `run-${runId}-manuscripts.${format}`,
    );
  },

  async wikibase(runId: string, format: WikibaseExportFormat = "json"): Promise<void> {
    const params = new URLSearchParams({format});
    await downloadAttachment(
      `/api/runs/${runId}/wikibase/export?${params}`,
      `run-${runId}-wikibase.${format}`,
    );
  },

  async wikidataStudio(
    runId: string,
    format: WikidataExportFormat = "json",
    approvedOnly = true,
  ): Promise<void> {
    const params = new URLSearchParams({format, approved_only: String(approvedOnly)});
    await downloadAttachment(
      `/api/runs/${runId}/wikidata-studio/export?${params}`,
      `run-${runId}-wikidata-${approvedOnly ? "approved" : "all"}.${format}`,
    );
  },
};

async function downloadAttachment(url: string, fallback: string): Promise<void> {
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    cache: "no-store",
    headers: {...csrfHeaders("GET")},
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json() as {detail?: unknown};
      if (typeof data?.detail === "string") detail = data.detail;
    } catch { /* not JSON */ }
    throw new Error(`Export failed: ${res.status} ${detail}`);
  }
  const blob = await res.blob();
  const filename = parseFilename(res.headers.get("content-disposition")) || fallback;
  triggerDownload(blob, filename);
}

function parseFilename(cd: string | null): string | null {
  if (!cd) return null;
  const star = cd.match(/filename\*\s*=\s*([^']*)'[^']*'([^;]+)/i);
  if (star?.[2]) {
    try { return decodeURIComponent(star[2].trim()); } catch { return star[2].trim(); }
  }
  const quoted = cd.match(/filename\s*=\s*"([^"]+)"/i);
  if (quoted?.[1]) return quoted[1];
  const bare = cd.match(/filename\s*=\s*([^;]+)/i);
  return bare?.[1]?.trim() ?? null;
}

function triggerDownload(blob: Blob, filename: string): void {
  if (typeof document === "undefined" || typeof URL === "undefined") return;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
