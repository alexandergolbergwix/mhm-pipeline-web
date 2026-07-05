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

  async wikibaseItems(runId: string, file: File): Promise<ImportResult> {
    const text = await file.text();
    const parsed = JSON.parse(text) as {items?: unknown[]};
    const items = Array.isArray(parsed.items) ? parsed.items : (Array.isArray(parsed) ? parsed : []);
    const res = await fetch(`/api/runs/${runId}/hmo-studio/items/import`, {
      method: "POST",
      credentials: "include",
      headers: {"Content-Type": "application/json", ...csrfHeaders("POST")},
      body: JSON.stringify({items}),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const data = await res.json() as {detail?: unknown};
        if (typeof data?.detail === "string") detail = data.detail;
      } catch { /* ignore */ }
      throw new Error(`Import failed: ${res.status} ${detail}`);
    }
    const body = await res.json() as {imported: number; skipped: number; errors: string[]};
    return {
      imported: body.imported,
      skipped: body.skipped,
      errors: (body.errors ?? []).map((message, i) => ({row: i + 1, message})),
    };
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
