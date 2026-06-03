/**
 * Per-section import API client.
 *
 * All functions POST a ``multipart/form-data`` file to the backend
 * and return an ``ImportResult`` with counts and per-row errors.
 *
 * Endpoints called (backend/app/routers/section_import.py):
 *   POST /api/runs/{id}/extraction/import
 *   POST /api/runs/{id}/authority/import
 *   POST /api/runs/{id}/rdf/import
 *   POST /api/runs/{id}/wikibase/import
 *   POST /api/runs/{id}/wikidata-studio/import
 */

import {csrfHeaders} from "@/api/client";

export interface ImportRowError {
  row: number;
  message: string;
}

export interface ImportResult {
  imported: number;
  skipped: number;
  errors: ImportRowError[];
}

export const SectionImport = {
  extraction(runId: string, file: File): Promise<ImportResult> {
    return uploadFile(`/api/runs/${runId}/extraction/import`, file);
  },
  authority(runId: string, file: File): Promise<ImportResult> {
    return uploadFile(`/api/runs/${runId}/authority/import`, file);
  },
  rdf(runId: string, file: File): Promise<ImportResult> {
    return uploadFile(`/api/runs/${runId}/rdf/import`, file);
  },
  wikibase(runId: string, file: File): Promise<ImportResult> {
    return uploadFile(`/api/runs/${runId}/wikibase/import`, file);
  },
  wikidataStudio(runId: string, file: File): Promise<ImportResult> {
    return uploadFile(`/api/runs/${runId}/wikidata-studio/import`, file);
  },
};

async function uploadFile(url: string, file: File): Promise<ImportResult> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(url, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    headers: {...csrfHeaders("POST")},
    body: fd,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json() as {detail?: unknown};
      if (typeof data?.detail === "string") detail = data.detail;
    } catch { /* not JSON */ }
    throw new Error(`Import failed: ${res.status} ${detail}`);
  }
  return res.json() as Promise<ImportResult>;
}
