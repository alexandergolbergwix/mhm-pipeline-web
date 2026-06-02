/**
 * Typed client for the project-export endpoints.
 *
 * NOT routed through ``api.*`` (which expects ``application/json``
 * and tries to parse the response). The export endpoints stream the
 * payload back with ``Content-Disposition: attachment`` so we use
 * raw ``fetch()`` and trigger a browser download from the resulting
 * Blob.
 *
 * Endpoints called (sibling agent owns ``backend/app/routers/export.py``):
 *   - GET /api/projects/{id}/export?entity_types=...
 *   - GET /api/projects/{id}/export/snapshots?entity_type=...&since=...
 *   - GET /api/projects/{id}/export/history?entity_type=...&entity_id=...
 *
 * All three return ``application/json`` as an attachment.
 */

import { csrfHeaders } from "@/api/client";

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
  /** Full project export. ``entityTypes`` filters which entity-type
   *  collections are included; omitted / empty array = all five. */
  async project(
    projectId: string,
    entityTypes?: ExportEntityType[],
  ): Promise<void> {
    const params = new URLSearchParams();
    if (entityTypes && entityTypes.length > 0) {
      for (const t of entityTypes) params.append("entity_types", t);
    }
    const qs = params.toString() ? `?${params.toString()}` : "";
    const fallback = `project-${projectId}-export.json`;
    await downloadAttachment(
      `/api/projects/${projectId}/export${qs}`,
      fallback,
    );
  },

  /** Snapshot export — point-in-time state of one entity type
   *  (or all five when ``entity_type`` is omitted). ``since`` is an
   *  ISO-8601 timestamp; backend filters to snapshots newer than it. */
  async snapshots(projectId: string, opts?: SnapshotsOptions): Promise<void> {
    const params = new URLSearchParams();
    if (opts?.entity_type) params.set("entity_type", opts.entity_type);
    if (opts?.since)       params.set("since", opts.since);
    const qs = params.toString() ? `?${params.toString()}` : "";
    const fallback = `project-${projectId}-snapshots.json`;
    await downloadAttachment(
      `/api/projects/${projectId}/export/snapshots${qs}`,
      fallback,
    );
  },

  /** Event-history export. Pass ``entity_type`` + ``entity_id`` to
   *  scope to a single entity; omit both for the full project event
   *  log. */
  async history(projectId: string, opts: HistoryOptions): Promise<void> {
    const params = new URLSearchParams();
    if (opts.entity_type) params.set("entity_type", opts.entity_type);
    if (opts.entity_id)   params.set("entity_id", opts.entity_id);
    const qs = params.toString() ? `?${params.toString()}` : "";
    const fallback = `project-${projectId}-history.json`;
    await downloadAttachment(
      `/api/projects/${projectId}/export/history${qs}`,
      fallback,
    );
  },
};

/** Fire a GET against ``url``, blob the body, trigger a download. */
async function downloadAttachment(url: string, fallback: string): Promise<void> {
  const res = await fetch(url, {
    method:      "GET",
    credentials: "include",
    cache:       "no-store",
    headers:     { ...csrfHeaders("GET") },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (data && typeof data === "object" && "detail" in data) {
        const raw = (data as { detail?: unknown }).detail;
        if (typeof raw === "string") detail = raw;
      }
    } catch {
      /* not JSON — keep statusText */
    }
    throw new Error(`Export failed: ${res.status} ${detail}`);
  }
  const blob = await res.blob();
  const filename = parseFilename(res.headers.get("content-disposition")) || fallback;
  triggerDownload(blob, filename);
}

/** Extract the ``filename=`` parameter from a ``Content-Disposition``
 *  header. Handles both quoted (``filename="x.json"``) and unquoted
 *  (``filename=x.json``) forms plus RFC 5987 ``filename*=UTF-8''x``.
 *  Returns ``null`` when no usable value is found. */
function parseFilename(cd: string | null): string | null {
  if (!cd) return null;
  // RFC 5987 takes precedence (e.g. ``filename*=UTF-8''project%20.json``)
  const star = cd.match(/filename\*\s*=\s*([^']*)'[^']*'([^;]+)/i);
  if (star && star[2]) {
    try {
      return decodeURIComponent(star[2].trim());
    } catch {
      return star[2].trim();
    }
  }
  const quoted = cd.match(/filename\s*=\s*"([^"]+)"/i);
  if (quoted && quoted[1]) return quoted[1];
  const bare = cd.match(/filename\s*=\s*([^;]+)/i);
  if (bare && bare[1]) return bare[1].trim();
  return null;
}

/** Synthesize a hidden <a download> click to save ``blob`` as
 *  ``filename``. Cleans up the object URL afterwards. */
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
  // Defer revoke so the browser has time to start the download.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
